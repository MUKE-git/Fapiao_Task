"""Explicit two-request C smoke test. Synthetic fixtures; no real invoice uploaded."""
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import toml
from PIL import Image, ImageDraw

from ai_client import ModelFileInput, extract_single_file, call_ai_audit_and_rename
from model_gateway import C_MODELS, qwen_call, validate_models


def main():
    if sys.argv[1:] != ["--run-two-requests"]:
        raise SystemExit("Requires --run-two-requests; consumes up to two API calls")
    secrets = toml.load('.streamlit/secrets.toml')
    validate_models(secrets.get('DASHSCOPE_MODEL', ''), secrets.get('DASHSCOPE_VISION_MODEL', ''))
    key = secrets['DASHSCOPE_API_KEY']
    cfg = SimpleNamespace(dashscope_api_key=key, dashscope_model=C_MODELS[0], dashscope_vision_model=C_MODELS[1])
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
        return qwen_call(**kwargs)
    task = {'files': [{'local_path': str(pdf), 'attachment_original_name': 'invoice.pdf',
                      'identified_type': 'Invoice', 'local_pdf_type': 'Invoice',
                      'text_snapshot': 'INVOICE\nInvoice number: 12345678901234567890\nInvoice date: 2026-11-01\nTotal amount: 123.45\nSeller: ABC Company\nItem: Office supplies'}],
            'model_usage': {'config_source': 'secrets.toml'}}
    with patch('model_gateway.qwen_call', side_effect=once), patch('ai_client.time.sleep'):
        data, error, _, _ = call_ai_audit_and_rename(task, model=C_MODELS[0], api_key=key)
    reports.append(dict(kind='text_pdf', model=C_MODELS[0], trace=task['model_usage'], result=data, error=error))
    count = 0; trace = {}
    result = extract_single_file(ModelFileInput('01', 'synthetic.png', 'invoice.png', '图片', images=((image_bytes.getvalue(), 'image/png'),)), cfg, transport=once, sleep=lambda _: None, diagnostics=trace)
    reports.append(dict(kind='image', model=C_MODELS[1], trace=trace, result=result.__dict__))
    Path('c_online_evidence.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps([dict(kind=r['kind'], model=r['model'], trace=r['trace']) for r in reports], ensure_ascii=True))


if __name__ == '__main__':
    main()
