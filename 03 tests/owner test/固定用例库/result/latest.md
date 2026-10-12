# 18 条必选固定用例离线验证

本次 PDF 字段由各例 `extract.json` 受控提供。通过只证明输入、主流程、附件处理、数据组装、T2-3 匹配及 Excel/JSON 交接；不证明真实 PDF/AI 识别。

| 用例 | 变体 | 结果 | 差异 |
| --- | --- | --- | --- |
| C01 | v1 | PASS | 无 |
| C02 | v1 | PASS | 无 |
| C03 | v1 | PASS | 无 |
| C04 | v1 | PASS | 无 |
| C04 | a | PASS | 无 |
| C05 | v1 | PASS | 无 |
| C06 | v1 | PASS | 无 |
| C07 | v1 | PASS | 无 |
| C08 | v1 | PASS | 无 |
| C09 | v1 | PASS | 无 |
| C12 | partial | PASS | 无 |
| C12 | all_missing | PASS | 无 |
| C13 | v1 | PASS | 无 |
| C14 | v1 | PASS | 无 |
| C15 | v1 | PASS | 无 |
| C16 | d049 | PASS | 无 |
| C16 | d050 | PASS | 无 |
| C16 | d051_name | PASS | 无 |
| C16 | d051_no_name | PASS | 无 |
| C17 | v1 | PASS | 无 |
| C18 | v1 | FAIL | alert missing: 被多张发票竞争 |
| C19 | same_amount | PASS | 无 |
| C19 | no_evidence | PASS | 无 |
| C22 | all_miss | PASS | 无 |
| C22 | miss_then_hit | PASS | 无 |
| C22 | hit_miss_hit | PASS | 无 |

通过 25/26 个变体。
人工审核状态仍为待审核。
