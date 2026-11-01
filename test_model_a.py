import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from ai_client import ModelFileInput, extract_single_file, call_ai_audit_and_rename
from model_gateway import A_MODELS, C_MODELS, validate_models, validate_deepseek_url, model_call

URL = 'https://test-workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'


class ModelATests(unittest.TestCase):
    def test_pair_validation(self):
        self.assertEqual(validate_models(*A_MODELS), 'A')
        self.assertEqual(validate_models('deepseek-v4.1-flash', A_MODELS[1]), 'A')
        self.assertEqual(validate_models(*C_MODELS), 'C')
        for pair in [(A_MODELS[0], C_MODELS[1]), (C_MODELS[0], A_MODELS[1]), ('', A_MODELS[1])]:
            with self.assertRaises(ValueError): validate_models(*pair)

    def test_url_required(self):
        self.assertEqual(validate_deepseek_url(URL), URL)
        for url in ['', 'http://example.com/compatible-mode/v1', 'https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1']:
            with self.assertRaises(ValueError): validate_deepseek_url(url)

    def test_a_converts_images_and_preserves_text(self):
        completion = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))])
        with patch('openai.OpenAI') as factory, patch('model_gateway.qwen_call') as qwen:
            client = factory.return_value.__enter__.return_value
            client.chat.completions.create.return_value = completion
            for vision in (False, True):
                configured_model = A_MODELS[0] if vision else 'deepseek-v4.1-flash'
                content = [{'image': 'data:image/png;base64,AAAA'}, {'text': 'invoice'}] if vision else 'invoice'
                response = model_call(model=configured_model, api_key='offline', messages=[{'role':'user','content':content}], vision=vision, base_url=URL)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.output.text, '{"ok":true}')
                sent = client.chat.completions.create.call_args.kwargs
                self.assertEqual(sent['model'], configured_model)
                if vision: self.assertEqual(sent['messages'][0]['content'][0]['type'], 'image_url')
                else: self.assertEqual(sent['messages'][0]['content'], 'invoice')
            self.assertEqual(factory.call_args.kwargs['max_retries'], 0)
            qwen.assert_not_called()

    def test_failure_never_switches_to_qwen(self):
        with patch('openai.OpenAI') as factory, patch('model_gateway.qwen_call') as qwen:
            factory.return_value.__enter__.return_value.chat.completions.create.side_effect = RuntimeError('offline failure')
            with self.assertRaises(RuntimeError):
                model_call(model=A_MODELS[0], api_key='offline', messages=[], base_url=URL)
            qwen.assert_not_called()

    def test_common_extractor_parses_a_output(self):
        raw = '{"file_classifications":[{"file":"x.png","role":"Invoice"}],"invoice_number":"12345678901234567890","invoice_date":"2026-11-01","total_amount":"123.45","seller":"ABC","receipt_type":"Office"}'
        cfg = SimpleNamespace(dashscope_api_key='offline', dashscope_model='deepseek-v4.1-flash', dashscope_vision_model=A_MODELS[1], deepseek_base_url=URL)
        with patch('openai.OpenAI') as factory:
            factory.return_value.__enter__.return_value.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=raw))])
            for images in [(), ((b'fake', 'image/png'),)]:
                result = extract_single_file(ModelFileInput('01','x.png','x.png','图片', images=images), cfg)
                self.assertEqual(result.role, 'Invoice')
                self.assertEqual(result.fields['total_amount'], '123.45')
                self.assertEqual(factory.return_value.__enter__.return_value.chat.completions.create.call_args.kwargs['model'], A_MODELS[1] if images else 'deepseek-v4.1-flash')

    def test_legacy_pdf_uses_independent_a_transport(self):
        task = {'files': [{'local_path': 'invoice.pdf', 'identified_type': 'Invoice', 'text_snapshot': 'invoice'}]}
        raw = '{"invoice_number":"12345678901234567890","invoice_date":"2026-11-01","total_amount":"123.45","invoice_type":"Invoice","receipt_type":"Office","seller":"ABC","file_classifications":[{"file":"invoice.pdf","role":"Invoice"}]}'
        with patch('openai.OpenAI') as factory, patch('ai_client._rename_pdfs_with_audit', return_value=([], [])), patch('model_gateway.qwen_call') as qwen:
            factory.return_value.__enter__.return_value.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=raw))])
            data, error, _, _ = call_ai_audit_and_rename(task, model='deepseek-v4.1-flash', api_key='offline', deepseek_base_url=URL)
            self.assertEqual(data['total_amount'], '123.45')
            self.assertEqual(error, '')
            self.assertEqual(factory.return_value.__enter__.return_value.chat.completions.create.call_args.kwargs['model'], 'deepseek-v4.1-flash')
            qwen.assert_not_called()


if __name__ == '__main__': unittest.main()
