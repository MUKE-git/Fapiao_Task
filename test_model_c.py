import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import prompt_loader
from model_gateway import validate_models, qwen_call, C_MODELS


class ModelCTests(unittest.TestCase):
    def test_same_family_models_are_configurable(self):
        self.assertEqual(validate_models(*C_MODELS), 'C')
        self.assertEqual(validate_models('qwen-plus', C_MODELS[1]), 'C')
        for pair in [('', ''), ('unknown-model', C_MODELS[1]), (C_MODELS[0], 'deepseek-v4.1-flash-1017')]:
            with self.assertRaises(ValueError):
                validate_models(*pair)

    def test_prompt_edits_take_effect_without_restart(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'prompts.txt'
            with patch.object(prompt_loader, 'PROMPT_FILE', path):
                path.write_text('@@ pdf_text @@\none\n@@ END @@', encoding='utf-8')
                self.assertEqual(prompt_loader.load_prompt('pdf_text'), 'one')
                path.write_text('@@ pdf_text @@\ntwo\n@@ END @@', encoding='utf-8')
                self.assertEqual(prompt_loader.load_prompt('pdf_text'), 'two')
                for text in ['', '@@ pdf_text @@\n\n@@ END @@', '@@ pdf_text @@\nunterminated']:
                    path.write_text(text, encoding='utf-8')
                    with self.assertRaises(ValueError): prompt_loader.load_prompt('pdf_text')

    def test_failure_does_not_call_another_model(self):
        with patch('dashscope.Generation.call', return_value=SimpleNamespace(status_code=503)) as text, patch('dashscope.MultiModalConversation.call') as vision:
            response = qwen_call(model=C_MODELS[0], api_key='offline', messages=[])
            self.assertEqual(response.status_code, 503)
            text.assert_called_once()
            vision.assert_not_called()

    def test_text_and_image_use_separate_sdk_methods(self):
        with patch('dashscope.Generation.call', return_value=SimpleNamespace(status_code=200)) as text, patch('dashscope.MultiModalConversation.call', return_value=SimpleNamespace(status_code=200)) as vision:
            for is_vision in (False, True):
                qwen_call(model=C_MODELS[int(is_vision)], api_key='offline', messages=[], vision=is_vision)
            self.assertEqual(text.call_args.kwargs['model'], C_MODELS[0])
            self.assertEqual(vision.call_args.kwargs['model'], C_MODELS[1])

    def test_all_live_sections_exist(self):
        for section in ('pdf_text', 'single_file', 'image_extra', 'arbitration'):
            self.assertTrue(prompt_loader.load_prompt(section).strip())


if __name__ == '__main__': unittest.main()
