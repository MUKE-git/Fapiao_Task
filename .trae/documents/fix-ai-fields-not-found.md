# 修复：AI 不返回新增字段导致全部 Not Found

## 一、根因分析

通过 `task_debug.json` 确认，AI（qwen3.5-plus）实际返回的 JSON：
```json
{
    "total_amount": 1.0,
    "invoice_number": "26447000000666344808",
    "invoice_date": "2026-03-25",
    "travel_time": "2026-03-25 09:04:53",
    "data_source": "T3_Chuxing"
}
```
缺少：`invoice_type`, `receipt_type`, `seller`, `trip_type`, `origin_dest`, `file_classifications`

**原因 1：提示词矛盾** — 第 698 行写“提取七个核心字段”，但实际给了 9 条 Extraction Rules，AI 可能只按“七个”处理。

**原因 2：无字段校验** — AI 返回 JSON 缺少字段时，代码仅用 `.get(k, "Not Found")` 静默兜底，没有任何日志告警，问题被掩盖。

## 二、修复方案

### 修复 1：修正提示词矛盾 + 强化必填约束

**文件：** `invoice_pipeline.py` `call_ai_audit_and_rename()` 中 `system_prompt`

改动 1（第 698 行）：`"提取七个核心字段"` → `"提取以下所有字段"`

改动 2：在 Constraints 区域新增一条强制约束：
```
- **每个字段都必须出现在 JSON 输出中**，即使值为 "Not Found" 也不能省略该字段。
```

### 修复 2：增加 AI 返回字段完整性校验

**文件：** `invoice_pipeline.py` `call_ai_audit_and_rename()` 中 JSON 解析成功后的逻辑（第 799-806 行）

在 `data = json.loads(res_text)` 成功后，增加字段补全逻辑：

```python
# 确保所有期望字段存在，缺失的补 "Not Found" 并记录
expected_fields = [
    "total_amount", "invoice_number", "invoice_date", "travel_time",
    "invoice_type", "receipt_type", "seller",
    "trip_type", "origin_dest", "file_classifications",
]
missing = [f for f in expected_fields if f not in data]
if missing:
    for f in missing:
        data[f] = "Not Found" if f != "file_classifications" else []
    error_reason = (error_reason + ";AI_MISSING_FIELDS:" + ",".join(missing)).strip(";")
```

### 修复 3：增加 text_snapshot 截取长度

**文件：** `invoice_pipeline.py` 第 988 行和 1012 行附近

当前：`"text_snapshot": f_text[:800]` 

改为：`"text_snapshot": f_text[:3000]`

800 字符可能截断了发票类型、销售方等关键信息，3000 字符更安全。

## 三、不涉及的内容

- 不改变模型选择（仍由 secrets.toml 配置）
- 不改变 Excel 列结构
- 不改变 Not Found 过滤逻辑

## 四、验证步骤

1. 运行后检查 `task_debug.json`，确认 `audit_result` 包含全部 9 个字段
2. 检查 Excel 汇总表，新增字段不再全是 Not Found
3. 检查日志，确认是否有 `AI_MISSING_FIELDS` 告警