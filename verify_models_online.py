"""Explicit two-request configured-model smoke test. Synthetic fixtures; no real invoice uploaded."""
import io
import os
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import toml
from PIL import Image, ImageDraw

from ai_client import ModelFileInput, extract_single_file, call_ai_audit_and_rename
from model_gateway import model_call, validate_models, validate_deepseek_url


def main():
    text_only = sys.argv[1:] == ["--run-text-once"]
    image_only = sys.argv[1:] == ["--run-image-once"]
    if not text_only and not image_only and sys.argv[1:] != ["--run-two-requests"]:
        raise SystemExit("Requires --run-text-once, --run-image-once or --run-two-requests")
    secrets = toml.load('.streamlit/secrets.toml')
    validate_models(secrets.get('DASHSCOPE_MODEL', ''), secrets.get('DASHSCOPE_VISION_MODEL', ''))
    models = (secrets['DASHSCOPE_MODEL'], secrets['DASHSCOPE_VISION_MODEL'])
    url = secrets.get('DEEPSEEK_BASE_URL', '')
    if validate_models(*models) == 'A': validate_deepseek_url(url)
    key = (os.getenv('DASHSCOPE_API_KEY') or secrets.get('DASHSCOPE_API_KEY') or '').strip()
    if not key:
        raise SystemExit('DASHSCOPE_API_KEY 未配置；未发起请求，不消耗调用额度。请在 secrets.toml 恢复该配置，或设置环境变量。')
    cfg = SimpleNamespace(dashscope_api_key=key, dashscope_model=models[0], dashscope_vision_model=models[1], deepseek_base_url=url, dashscope_model_source='secrets.toml', dashscope_vision_model_source='secrets.toml')
    reports = []
    image = Image.new('RGB', (1100, 650), 'white')
    ImageDraw.Draw(image).text((50, 60), 'INVOICE\nInvoice number: 12345678901234567890\nInvoice date: 2026-11-01\nTotal amount: 123.45\nSeller: ABC Company\nItem: Office supplies', fill='black', font_size=30)
    image_bytes = io.BytesIO(); image.save(image_bytes, format='PNG')
    root = Path('.test-tmp/c-online');root.mkdir(parents=True, exist_ok=True)
    pdf = root / 'synthetic_invoice.pdf';image.save(pdf, format='PDF')
    # A guard prevents SDK/processor application retries from consuming extra calls.
    count = 0
    def once(**kwargs):
        nonlocal count
        count += 1
        if count > 1:
            raise RuntimeError('REQUEST_BUDGET_EXHAUSTED')
        kwargs.setdefault('source', 'secrets.toml')
        kwargs.setdefault('base_url', url)
        return model_call(**kwargs)
    task = {'files': [{'local_path': str(pdf), 'attachment_original_name': 'invoice.pdf',
                      'identified_type': 'Invoice', 'local_pdf_type': 'Invoice',
                      'text_snapshot': 'INVOICE\nInvoice number: 12345678901234567890\nInvoice date: 2026-11-01\nTotal amount: 123.45\nSeller: ABC Company\nItem: Office supplies'}],
            'model_usage': {'config_source': 'secrets.toml'}}
    if not image_only:
        with patch('model_gateway.model_call', side_effect=once), patch('ai_client.time.sleep'):
            data, error, _, _ = call_ai_audit_and_rename(task, model=models[0], api_key=key, deepseek_base_url=url)
        reports.append(dict(kind='text_pdf', model=models[0], trace=task['model_usage'], result=data, error=error))
    if text_only:
        Path('a_text_online_evidence.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        print(json.dumps(dict(kind='text_pdf', model=models[0], trace=task['model_usage'], error=error), ensure_ascii=True))
        return
    count = 0; trace = {}
    result = extract_single_file(ModelFileInput('01', 'synthetic.png', 'invoice.png', '图片', images=((image_bytes.getvalue(), 'image/png'),)), cfg, transport=once, sleep=lambda _: None, diagnostics=trace)
    reports.append(dict(kind='image', model=models[1], trace=trace, result=result.__dict__))
    Path('configured_model_online_evidence.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps([dict(kind=r['kind'], model=r['model'], trace=r['trace']) for r in reports], ensure_ascii=True))


if __name__ == '__main__':
    main()
