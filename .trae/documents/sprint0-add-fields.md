# Sprint 0 补充：新增 Excel 字段（邮件号码、行程类型、出发地/目的地）

## 一、概述

基于用户提供的数据库 Schema（`invoice_records`、`itinerary_records`），在 Sprint 0 已有的 7 列基础上，新增 3 列：邮件号码（`task_id`）、行程类型（`trip_type`）、出发地/目的地（`origin_dest`）。

## 二、当前状态

**已有 Excel 列（7 列）：** 发票日期、发票号码、报销金额、发票类型、票面类型、出行时间、销售方

**新增 3 列后共 10 列：**
| 序号 | 表头 | 键名 | 数据来源 |
|:---|:---|:---|:---|
| 1 | 发票日期 | `invoice_date` | AI 提取 |
| 2 | 发票号码 | `invoice_number` | AI 提取 |
| 3 | 报销金额 | `total_amount` | AI 提取 |
| 4 | 发票类型 | `invoice_type` | AI 提取 |
| 5 | 票面类型 | `receipt_type` | AI 提取 |
| 6 | 出行时间 | `travel_time` | AI 提取 |
| 7 | 销售方 | `seller` | AI 提取 |
| 8 | **邮件号码** | `task_id` | **系统生成（IMAP 邮件 ID）** |
| 9 | **行程类型** | `trip_type` | **AI 提取（行程单）** |
| 10 | **出发地/目的地** | `origin_dest` | **AI 提取（行程单）** |

## 三、变更清单

### 变更 1：SUMMARY_COLUMNS 扩展

**文件：** `invoice_pipeline.py` 第 30-38 行

追加 3 个新列：
```python
SUMMARY_COLUMNS: List[Tuple[str, str]] = [
    ("发票日期", "invoice_date"),
    ("发票号码", "invoice_number"),
    ("报销金额", "total_amount"),
    ("发票类型", "invoice_type"),
    ("票面类型", "receipt_type"),
    ("出行时间", "travel_time"),
    ("销售方", "seller"),
    ("邮件号码", "task_id"),
    ("行程类型", "trip_type"),
    ("出发地/目的地", "origin_dest"),
]
```

### 变更 2：AI Prompt 更新

**文件：** `invoice_pipeline.py` `call_ai_audit_and_rename()` 中 `system_prompt`

新增两条 Extraction Rules：

```
8. **行程类型 (trip_type)**：
   - 必须从【行程单】文本中提取。
   - 判断出行方式，如“机票”、“高铁”、“火车”、“滴滴”、“网约车”、“出租车”等。
   - 若行程单中无明确出行方式，填写 "Not Found"。
9. **出发地/目的地 (origin_dest)**：
   - 必须从【行程单】文本中提取。
   - 格式为“出发地 → 目的地”，如“北京 → 上海”、“杭州东 → 南京南”。
   - 若行程单包含多段行程，只提取第一段的起终点。
   - 若无法提取，填写 "Not Found"。
```

更新 Output Format 示例 JSON，增加 `trip_type` 和 `origin_dest` 字段。

### 变更 3：build_fallback_audit_result 扩展

**文件：** `invoice_pipeline.py` 第 215-266 行

新增两个变量和正则兜底：
- `trip_type`: 正则从行程单文本中匹配“机票/高铁/火车/滴滴/网约车/出租车”等关键词，默认 `"Not Found"`
- `origin_dest`: 正则从行程单文本中匹配出发地→目的地模式，默认 `"Not Found"`

返回字典新增 `trip_type` 和 `origin_dest` 字段。

### 变更 4：run_pipeline() excel_rows 组装

**文件：** `invoice_pipeline.py` 第 1099-1107 行

在 `excel_rows.append()` 字典中新增 3 个字段：
- `task_id`: 取 `msg_id_str`（当前邮件 IMAP ID，已在循环上下文中可用）
- `trip_type`: 取 `audit_res.get("trip_type", "Not Found")`
- `origin_dest`: 取 `audit_res.get("origin_dest", "Not Found")`

### 变更 5：_collect_not_found_alerts 扩展

**文件：** `invoice_pipeline.py` 第 824-857 行

对 `trip_type` 和 `origin_dest` 的检查：仅在邮件包含行程单（`has_it` 为 True）时，检查这两个字段是否为 Not Found，若是则加入 flags。

## 四、不涉及的内容

- `group_tag`（分组标识）暂不实现，需后续架构变更支持同邮件多组文件分组
- `task_id` 仅作为 Excel 列写入，不改变前端 UI 展示
- 不改变 Not Found 过滤逻辑（`task_id` 是系统字段不会为 Not Found，`trip_type`/`origin_dest` 不是关键字段不影响写入判断）

## 五、验证步骤

1. 运行后检查 `发票信息汇总表.xlsx` 是否包含 10 列，顺序正确
2. 确认 `邮件号码` 列与 IMAP 邮件 ID 一致
3. 处理含行程单的邮件，确认 `行程类型` 和 `出发地/目的地` 有正确值
4. 处理不含行程单的邮件，确认 `行程类型` 和 `出发地/目的地` 显示 "Not Found"