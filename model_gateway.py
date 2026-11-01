"""Select independent transports by family; send configured model names unchanged."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

C_MODELS = ("qwen3.7-max", "qwen3-vl-plus")
A_MODELS = ("deepseek-v4.1-flash-1017",) * 2


def record_processing(trace):
    path = Path(__file__).parent / "04 logs" / "model_calls.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    allowed = ("configured_model", "config_source", "model_kind", "call_attempted", "successful_response", "result", "extraction_source")
    record = {key: trace[key] for key in allowed if key in trace}
    record.update(timestamp=datetime.now(timezone.utc).isoformat(), event="processing_result")
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def model_family(model: str) -> str:
    import re
    if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', model):
        raise ValueError('模型名缺失或格式错误，请明确填写 DASHSCOPE_MODEL 和 DASHSCOPE_VISION_MODEL。')
    if model.startswith('deepseek-'):
        return 'A'
    if model.startswith('qwen'):
        return 'C'
    raise ValueError('尚未实现该模型家族的接口：' + model)


def validate_models(text: str, vision: str) -> str:
    family = model_family(text)
    if model_family(vision) != family:
        raise ValueError('文本和图片模型必须同属 DeepSeek 或千问；同家族可分别填写不同型号。')
    return family


def qwen_call(*, model, api_key, messages, vision=False, source="programmatic", sdk_call=None, **kwargs):
    from dashscope import Generation, MultiModalConversation
    if model_family(model) != 'C':
        raise ValueError("千问调用实现收到不支持的模型：" + model)
    record = dict(timestamp=datetime.now(timezone.utc).isoformat(), implementation="qwen",
                  configured_model=model, config_source=source, model_kind="vision" if vision else "text",
                  call_attempted=False, successful_response=False, result="not_started")
    path = Path(__file__).parent / "04 logs" / "model_calls.jsonl"
    def write():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    if not api_key.strip():
        record["result"] = "API_KEY_MISSING"
        write()
        raise ValueError("DASHSCOPE_API_KEY 未配置")
    record.update(call_attempted=True, result="request_started")
    write()
    try:
        call = sdk_call or (MultiModalConversation.call if vision else Generation.call)
        response = call(api_key=api_key, model=model, messages=messages, **kwargs)
        status = int(getattr(response, "status_code", 0) or 0)
        record.update(successful_response=status == 200, result="response_received" if status == 200 else f"HTTP_{status}")
        return response
    except Exception as exc:
        record["result"] = "TRANSPORT_" + type(exc).__name__
        raise
    finally:
        write()


def validate_deepseek_url(value):
    from urllib.parse import urlsplit
    url = urlsplit(value)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.path.rstrip('/') != '/compatible-mode/v1'
            or not url.hostname.isascii() or not url.hostname.endswith('.aliyuncs.com') or '{' in value or '}' in value):
        raise ValueError('DEEPSEEK_BASE_URL 必须填写真实百炼 HTTPS 兼容接口地址，含 /compatible-mode/v1；请替换业务空间占位符。')
    return value.rstrip('/')


def model_call(*, model, api_key, messages, vision=False, source='programmatic', base_url='', **kwargs):
    if model_family(model) == 'A':
        return deepseek_call(model=model, api_key=api_key, messages=messages, vision=vision,
                             source=source, base_url=base_url)
    return qwen_call(model=model, api_key=api_key, messages=messages, vision=vision, source=source, **kwargs)


def deepseek_call(*, model, api_key, messages, vision=False, source='programmatic', base_url=''):
    """Independent OpenAI-compatible transport; normalize response for existing processors."""
    from types import SimpleNamespace
    trace = dict(configured_model=model, config_source=source, model_kind='vision' if vision else 'text',
                 call_attempted=False, successful_response=False, result='not_started')
    try:
        if model_family(model) != 'A':
            raise ValueError('DeepSeek 实现收到不支持的模型')
        url = validate_deepseek_url(base_url)
        if not api_key.strip():
            raise ValueError('DASHSCOPE_API_KEY 未配置')
        converted = []
        for message in messages:
            content = message['content']
            if isinstance(content, list):
                content = [({'type': 'image_url', 'image_url': {'url': part['image']}}
                            if 'image' in part else {'type': 'text', 'text': part['text']}) for part in content]
            converted.append({'role': message['role'], 'content': content})
        from openai import OpenAI
        # Disable SDK retries: application diagnostics account for every attempt.
        with OpenAI(api_key=api_key, base_url=url, max_retries=0, timeout=90.0) as client:
            trace.update(call_attempted=True, result='request_started')
            record_processing(trace)
            response = client.chat.completions.create(model=model, messages=converted,
                                                      extra_body={'enable_thinking': False})
        text = response.choices[0].message.content or ''
        trace.update(successful_response=True, result='response_received')
        return SimpleNamespace(status_code=200, output=SimpleNamespace(text=text, choices=[{'message': {'content': text}}]))
    except Exception as exc:
        trace['result'] = 'DEEPSEEK_' + type(exc).__name__
        status = getattr(exc, 'status_code', None)
        if isinstance(status, int):
            trace['result'] = f'DEEPSEEK_HTTP_{status}'
            return SimpleNamespace(status_code=status, code='DEEPSEEK_HTTP_ERROR', output=None)
        raise
    finally:
        record_processing(trace)
