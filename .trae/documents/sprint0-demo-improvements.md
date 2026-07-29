# Sprint 0：Demo 最小完善计划

## 一、概述

当前 `Fapiao_Task` 是一个发票邮件自动处理 Demo，包含 Streamlit 前端（`app_demo.py`）和核心流水线（`invoice_pipeline.py`）。本次 Sprint 0 目标是在发放第一波用户前完成 5 项最基本的完善。

## 二、当前状态分析

### 现有架构
- `app_demo.py`（242行）：Streamlit UI，表单收集 IMAP 信息 + 调用 `run_pipeline`
- `invoice_pipeline.py`（1106行）：核心流水线（IMAP收信 → PDF解析 → AI提取 → Excel写入 → PDF重命名）
- `.streamlit/secrets.toml.example`：仅含 `DASHSCOPE_API_KEY` 一项配置

### 当前问题
| # | 问题 | 位置 |
|---|------|------|
| 1 | 模型名硬编码为 `qwen-plus` | `invoice_pipeline.py:717` |
| 2 | API 调用无重试机制 | `invoice_pipeline.py:715-739` |
| 3 | Excel 汇总表仅 3 列（发票日期/发票号码/报销金额） | `invoice_pipeline.py:30-34` |
| 4 | Not Found 字段直接写入 Excel | `invoice_pipeline.py:1039-1043` |
| 5 | PDF 重命名模板固定（如 `客运服务费+金额元`），不支持类型+日期 | `invoice_pipeline.py:627-632` |

## 三、变更清单

### 变更 1：模型可配置化（secrets.toml）

**涉及文件：**
- `.streamlit/secrets.toml.example`
- `app_demo.py`
- `invoice_pipeline.py`

**修改内容：**

1. **`secrets.toml.example`**：新增 `DASHSCOPE_MODEL` 配置项
   ```
   DASHSCOPE_MODEL = "qwen3-max"
   ```

2. **`app_demo.py`**：新增 `_dashscope_model()` 函数，从环境变量 `DASHSCOPE_MODEL` 或 `st.secrets` 读取模型名，默认 `"qwen-plus"`；在构建 `RunConfig` 时传入。

3. **`invoice_pipeline.py`**：
   - `RunConfig` 新增 `dashscope_model: str = "qwen-plus"` 字段
   - `call_ai_audit_and_rename()` 新增 `model` 参数，替换硬编码的 `'qwen-plus'`
   - `run_pipeline()` 将 `cfg.dashscope_model` 传入 `call_ai_audit_and_rename()`

### 变更 2：API 重试机制

**涉及文件：** `invoice_pipeline.py`

**修改内容：**

在 `call_ai_audit_and_rename()` 中，将当前的单次 API 调用包裹为重试循环：
- 重试条件：HTTP 429 或 5xx
- 最大重试次数：2 次
- 重试间隔：第1次重试 1s，第2次重试 3s
- 重试耗尽后走 `build_fallback_audit_result` 兜底
- 其他错误（4xx 非429）不重试，直接走兜底

具体实现：将第 715-737 行的 `Generation.call()` 调用改为 `for retry in range(3)` 循环，每次根据 `response.status_code` 判断是否可重试。

### 变更 3：Excel 字段扩展

**涉及文件：** `invoice_pipeline.py`

**修改内容：**

1. **`SUMMARY_COLUMNS`（第30-34行）**：扩展为：
   ```python
   SUMMARY_COLUMNS: List[Tuple[str, str]] = [
       ("发票日期", "invoice_date"),
       ("发票号码", "invoice_number"),
       ("报销金额", "total_amount"),
       ("发票类型", "invoice_type"),
       ("票面类型", "receipt_type"),
       ("出行时间", "travel_time"),
       ("销售方", "seller"),
   ]
   ```

2. **AI Prompt（`call_ai_audit_and_rename` 中 `system_prompt`）**：新增提取字段说明：
   - `invoice_type`（发票类型）：如"增值税电子普通发票"、"增值税专用发票"等
   - `receipt_type`（票面类型）：如"客运服务费"、"餐饮服务费"等
   - `seller`（销售方）：开票方名称
   - 更新 Output Format 示例 JSON 包含这些字段

3. **`build_fallback_audit_result()`（第199-243行）**：新增返回字段：
   - `invoice_type`: 正则从发票文本中匹配"发票类型"或"发票名称"后提取，默认 `"Not Found"`
   - `receipt_type`: 正则匹配票面服务类型，默认 `"Not Found"`
   - `seller`: 正则匹配"销售方"或"名称"后的企业名，默认 `"Not Found"`

4. **`run_pipeline()` 中 `excel_rows` 组装（第1039-1043行）**：新增对应字段。

5. **`_collect_not_found_alerts()`（第774-809行）**：新增对 `invoice_type`、`receipt_type`、`seller` 的 Not Found 检查。

### 变更 4：Not Found 过滤

**涉及文件：** `invoice_pipeline.py`

**修改内容：**

在 `run_pipeline()` 第 1038-1043 行，`excel_rows.append()` 之前增加判断逻辑：
- 定义关键字段列表：`["invoice_date", "invoice_number", "total_amount"]`
- 若 `audit_res` 中任一关键字段值为 `"Not Found"` 或空字符串，则**不写入 Excel**
- 改为将该邮件信息追加到 `not_found_alerts`（通过 `_collect_not_found_alerts` 已有的逻辑覆盖）
- 同时向 `log` 输出提示信息

具体修改位置：`invoice_pipeline.py` 第 1038 行附近，将：
```python
if audit_res:
    excel_rows.append({...})
```
改为：
```python
if audit_res:
    has_not_found = any(
        str(audit_res.get(k, "")).strip() in ("", "Not Found")
        for k in ("invoice_date", "invoice_number", "total_amount")
    )
    if has_not_found:
        log(f"⚠️ 关键字段缺失，未写入 Excel: {subject}")
    else:
        excel_rows.append({...})
```

### 变更 5：PDF 重命名模板

**涉及文件：** `invoice_pipeline.py`

**修改内容：**

修改 `_rename_pdfs_with_audit()` 中第 627-632 行的重命名模板：

**当前代码：**
```python
if eff_n == "Invoice":
    base = f"{invoice_date_cn}+客运服务费+{amount_text}元"
elif eff_n == "Itinerary":
    base = f"行程单+{amount_text}元"
else:
    base = f"票据+{amount_text}元"
```

**改为：**
```python
# 日期用 YYYY-MM-DD 格式（从 audit_result 取 invoice_date 原始值）
inv_date_raw = str(data.get("invoice_date", "")).strip()
if not inv_date_raw or inv_date_raw == "Not Found":
    inv_date_raw = "未知日期"

if eff_n == "Invoice":
    inv_type = str(data.get("receipt_type") or data.get("invoice_type") or "发票").strip()
    base = f"{inv_type}+{inv_date_raw}+{amount_text}元"
elif eff_n == "Itinerary":
    base = f"行程单+{inv_date_raw}+{amount_text}元"
else:
    base = f"票据+{inv_date_raw}+{amount_text}元"
```

同时移除不再需要的 `format_invoice_date_cn` 调用（`_rename_pdfs_with_audit` 中第 571 行 `invoice_date_cn` 变量声明）。

## 四、不涉及的内容

以下内容不在本次 Sprint 0 范围内：
- 调试日志按 run_id 拆分（仍使用 `debug-70cc39.log`）
- 重复邮件防护
- Excel 权限错误优化
- 前端 UI 改动（除新增模型选择相关）
- 文档补全

## 五、验证步骤

1. **模型可配置化**：修改 `secrets.toml` 中的 `DASHSCOPE_MODEL` 为不同模型名，确认 API 调用使用对应模型
2. **重试机制**：模拟 API 返回 429/5xx，确认重试行为（最多2次、间隔1s/3s）
3. **Excel 字段**：运行后检查 `发票信息汇总表.xlsx` 是否包含 7 列，且顺序正确
4. **Not Found 过滤**：准备一份必缺字段的发票邮件，确认该行不写入 Excel，且前端提示"待人工核查"
5. **PDF 重命名**：处理后检查 PDF 文件名是否符合 `{类型}+{日期}+{金额}元.pdf` 格式