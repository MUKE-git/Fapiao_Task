# 固定邮件到最终输出：真实本地 PDF 整链路

固定 EML 模拟邮箱返回；项目实际解析附件、预读 PDF、分类、字段提取、匹配并输出。只模拟 AI 失败，不用 extract.json 替代处理结果。表中 PASS/FAIL 是与原冻结草案的比较；草案原用于受控提取，差异须审核，不能据此宣称所有差异都是程序缺陷。 实际调用检查完成 26/26 个变体。

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
| C09 | v1 | FAIL | record_count: expected 0, actual 1；alert missing: PDF_READ_ERROR |
| C12 | partial | FAIL | 发票.pdf.total_amount: expected '262.68', actual 'Not Found' |
| C12 | all_missing | PASS | 无 |
| C13 | v1 | FAIL | alert missing: 缺行程单 |
| C14 | v1 | FAIL | route_json 发票.pdf: expected '杭州东站→杭州宋城（之江路148号）', actual None；route_excel 发票.pdf: expected one row with '杭州东站→杭州宋城（之江路148号）', actual [{'invoice_date': '2026-09-27', 'invoice_number': '26331540109971408975', 'total_amount': 'Not Found', 'invoice_type': '发票类型：电子发票（普通发票）', 'receipt_type': 'Not Found', 'travel_time': None, 'seller': '合成测试销售方', 'origin_dest': None}]；matched_by_invoice: expected {'发票.pdf': '行程单甲.pdf'}, actual {} |
| C15 | v1 | FAIL | route_json 发票B.pdf: expected '浙江省科技馆（西湖文化广场2号）→杭州宋城', actual None；route_excel 发票B.pdf: expected one row with '浙江省科技馆（西湖文化广场2号）→杭州宋城', actual [{'invoice_date': '2026-09-28', 'invoice_number': '26331429265386194320', 'total_amount': 'Not Found', 'invoice_type': '发票类型：电子发票（普通发票）', 'receipt_type': 'Not Found', 'travel_time': None, 'seller': '合成测试销售方', 'origin_dest': None}]；matched_by_invoice: expected {'发票B.pdf': '行程单B.pdf'}, actual {} |
| C16 | d049 | FAIL | route_json 发票C.pdf: expected '杭州东站→杭州宋城', actual None；route_excel 发票C.pdf: expected one row with '杭州东站→杭州宋城', actual [{'invoice_date': '2026-09-28', 'invoice_number': '26331429192087625629', 'total_amount': 'Not Found', 'invoice_type': '发票类型：电子发票（普通发票）', 'receipt_type': 'Not Found', 'travel_time': None, 'seller': '合成测试销售方', 'origin_dest': None}]；matched_by_invoice: expected {'发票C.pdf': '行程单A.pdf'}, actual {} |
| C16 | d050 | FAIL | route_json 发票C.pdf: expected '杭州东站→杭州宋城', actual None；route_excel 发票C.pdf: expected one row with '杭州东站→杭州宋城', actual [{'invoice_date': '2026-09-28', 'invoice_number': '26331461668052080012', 'total_amount': 'Not Found', 'invoice_type': '发票类型：电子发票（普通发票）', 'receipt_type': 'Not Found', 'travel_time': None, 'seller': '合成测试销售方', 'origin_dest': None}]；matched_by_invoice: expected {'发票C.pdf': '行程单A.pdf'}, actual {} |
| C16 | d051_name | FAIL | route_json 发票A.pdf: expected '杭州东站→杭州宋城', actual None；route_excel 发票A.pdf: expected one row with '杭州东站→杭州宋城', actual [{'invoice_date': '2026-09-28', 'invoice_number': '26331626229746587002', 'total_amount': 'Not Found', 'invoice_type': '发票类型：电子发票（普通发票）', 'receipt_type': 'Not Found', 'travel_time': None, 'seller': '合成测试销售方', 'origin_dest': None}]；matched_by_invoice: expected {'发票A.pdf': '行程单A.pdf'}, actual {} |
| C16 | d051_no_name | PASS | 无 |
| C17 | v1 | FAIL | route_json 发票A.pdf: expected '杭州东站→千岛湖镇人民政府', actual None；route_excel 发票A.pdf: expected one row with '杭州东站→千岛湖镇人民政府', actual [{'invoice_date': '2026-09-26', 'invoice_number': '26331882617152424518', 'total_amount': 'Not Found', 'invoice_type': '发票类型：电子发票（普通发票）', 'receipt_type': 'Not Found', 'travel_time': None, 'seller': '合成测试销售方', 'origin_dest': None}]；matched_by_invoice: expected {'发票A.pdf': '行程单A.pdf'}, actual {}；alert missing: 金额缺失或格式/精度需核对 |
| C18 | v1 | FAIL | route_json 发票C.pdf: expected '临安汽车东站→杭州东站', actual None；route_excel 发票C.pdf: expected one row with '临安汽车东站→杭州东站', actual [{'invoice_date': '2026-09-28', 'invoice_number': '26331585693637935814', 'total_amount': 'Not Found', 'invoice_type': '发票类型：电子发票（普通发票）', 'receipt_type': 'Not Found', 'travel_time': None, 'seller': '合成测试销售方', 'origin_dest': None}]；matched_by_invoice: expected {'发票C.pdf': '行程单C.pdf'}, actual {}；alert missing: 被多张发票竞争 |
| C19 | same_amount | FAIL | alert missing: 候选无法唯一确定 |
| C19 | no_evidence | FAIL | alert missing: 金额与原名尾缀均未命中 |
| C22 | all_miss | PASS | 无 |
| C22 | miss_then_hit | FAIL | 发票甲.pdf.total_amount: expected '97.49', actual 'Not Found'；mail_checks.pdf_calls: expected ['发票甲.pdf'], actual ['Msg2_01_发票甲.pdf'] |
| C22 | hit_miss_hit | FAIL | 发票甲.pdf.total_amount: expected '355.29', actual 'Not Found'；发票乙.pdf.total_amount: expected '289.87', actual 'Not Found'；mail_checks.pdf_calls: expected ['发票甲.pdf', '发票乙.pdf'], actual ['Msg1_01_发票甲.pdf', 'Msg3_01_发票乙.pdf'] |

## 实际输出索引

| 用例 / 变体 | 预读 | 最终 JSON | Excel |
| --- | --- | --- | --- |
| C01 / v1 | [打开](local_flow/C01/v1/pre_read.json) | [打开](local_flow/C01/v1/final.json) | 无输出行 |
| C02 / v1 | [打开](local_flow/C02/v1/pre_read.json) | [打开](local_flow/C02/v1/final.json) | 无输出行 |
| C03 / v1 | [打开](local_flow/C03/v1/pre_read.json) | [打开](local_flow/C03/v1/final.json) | 无输出行 |
| C04 / v1 | [打开](local_flow/C04/v1/pre_read.json) | [打开](local_flow/C04/v1/final.json) | [打开](local_flow/C04/v1/final.xlsx) |
| C04 / a | [打开](local_flow/C04/a/pre_read.json) | [打开](local_flow/C04/a/final.json) | [打开](local_flow/C04/a/final.xlsx) |
| C05 / v1 | [打开](local_flow/C05/v1/pre_read.json) | [打开](local_flow/C05/v1/final.json) | [打开](local_flow/C05/v1/final.xlsx) |
| C06 / v1 | [打开](local_flow/C06/v1/pre_read.json) | [打开](local_flow/C06/v1/final.json) | [打开](local_flow/C06/v1/final.xlsx) |
| C07 / v1 | [打开](local_flow/C07/v1/pre_read.json) | [打开](local_flow/C07/v1/final.json) | [打开](local_flow/C07/v1/final.xlsx) |
| C08 / v1 | [打开](local_flow/C08/v1/pre_read.json) | [打开](local_flow/C08/v1/final.json) | [打开](local_flow/C08/v1/final.xlsx) |
| C09 / v1 | [打开](local_flow/C09/v1/pre_read.json) | [打开](local_flow/C09/v1/final.json) | 无输出行 |
| C12 / partial | [打开](local_flow/C12/partial/pre_read.json) | [打开](local_flow/C12/partial/final.json) | [打开](local_flow/C12/partial/final.xlsx) |
| C12 / all_missing | [打开](local_flow/C12/all_missing/pre_read.json) | [打开](local_flow/C12/all_missing/final.json) | 无输出行 |
| C13 / v1 | [打开](local_flow/C13/v1/pre_read.json) | [打开](local_flow/C13/v1/final.json) | [打开](local_flow/C13/v1/final.xlsx) |
| C14 / v1 | [打开](local_flow/C14/v1/pre_read.json) | [打开](local_flow/C14/v1/final.json) | [打开](local_flow/C14/v1/final.xlsx) |
| C15 / v1 | [打开](local_flow/C15/v1/pre_read.json) | [打开](local_flow/C15/v1/final.json) | [打开](local_flow/C15/v1/final.xlsx) |
| C16 / d049 | [打开](local_flow/C16/d049/pre_read.json) | [打开](local_flow/C16/d049/final.json) | [打开](local_flow/C16/d049/final.xlsx) |
| C16 / d050 | [打开](local_flow/C16/d050/pre_read.json) | [打开](local_flow/C16/d050/final.json) | [打开](local_flow/C16/d050/final.xlsx) |
| C16 / d051_name | [打开](local_flow/C16/d051_name/pre_read.json) | [打开](local_flow/C16/d051_name/final.json) | [打开](local_flow/C16/d051_name/final.xlsx) |
| C16 / d051_no_name | [打开](local_flow/C16/d051_no_name/pre_read.json) | [打开](local_flow/C16/d051_no_name/final.json) | [打开](local_flow/C16/d051_no_name/final.xlsx) |
| C17 / v1 | [打开](local_flow/C17/v1/pre_read.json) | [打开](local_flow/C17/v1/final.json) | [打开](local_flow/C17/v1/final.xlsx) |
| C18 / v1 | [打开](local_flow/C18/v1/pre_read.json) | [打开](local_flow/C18/v1/final.json) | [打开](local_flow/C18/v1/final.xlsx) |
| C19 / same_amount | [打开](local_flow/C19/same_amount/pre_read.json) | [打开](local_flow/C19/same_amount/final.json) | [打开](local_flow/C19/same_amount/final.xlsx) |
| C19 / no_evidence | [打开](local_flow/C19/no_evidence/pre_read.json) | [打开](local_flow/C19/no_evidence/final.json) | [打开](local_flow/C19/no_evidence/final.xlsx) |
| C22 / all_miss | [打开](local_flow/C22/all_miss/pre_read.json) | [打开](local_flow/C22/all_miss/final.json) | 无输出行 |
| C22 / miss_then_hit | [打开](local_flow/C22/miss_then_hit/pre_read.json) | [打开](local_flow/C22/miss_then_hit/final.json) | [打开](local_flow/C22/miss_then_hit/final.xlsx) |
| C22 / hit_miss_hit | [打开](local_flow/C22/hit_miss_hit/pre_read.json) | [打开](local_flow/C22/hit_miss_hit/final.json) | [打开](local_flow/C22/hit_miss_hit/final.xlsx) |

通过 12/26 个变体。
人工审核状态仍为待审核。
