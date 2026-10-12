# 固定用例：真实本地 PDF 阶段实测

实际运行 `local_inspect_pdf`、原名合并分类、正则兜底及 `process_pdf`；只把 AI HTTP 响应模拟为 401，不连接邮箱或 AI。
本报告是**实际输出**；`extract.json` 是受控接口草案，人工标准答案仍待审核。
PDF 组件逐份运行，包含主流程因主题不匹配而不会处理的附件。

18 条用例、23 个变体、46 份 PDF；可读 45 份；基础检查全部通过 46 份；需关注 0 份；与受控角色草案不同 1 份；字段对照有差异 45 份（两者属于不同处理层，差异不直接算失败）。

## 用例索引（保持原顺序）

| 用例 | 变体 | PDF 数 | 原审核页 |
| --- | --- | ---: | --- |
| C01 | v1 | 0 | [打开](<../cases/C01/review.md>) |
| C02 | v1 | 1 | [打开](<../cases/C02/review.md>) |
| C03 | v1 | 0 | [打开](<../cases/C03/review.md>) |
| C04 | v1 | 1 | [打开](<../cases/C04/review.md>) |
| C05 | v1 | 2 | [打开](<../cases/C05/review.md>) |
| C06 | v1 | 1 | [打开](<../cases/C06/review.md>) |
| C07 | v1 | 3 | [打开](<../cases/C07/review.md>) |
| C08 | v1 | 1 | [打开](<../cases/C08/review.md>) |
| C09 | v1 | 1 | [打开](<../cases/C09/review.md>) |
| C11 | v1 | 1 | [打开](<../cases/C11/review.md>) |
| C12 | partial | 1 | [打开](<../cases/C12/review.md>) |
| C12 | all_missing | 1 | [打开](<../cases/C12/review.md>) |
| C13 | v1 | 1 | [打开](<../cases/C13/review.md>) |
| C14 | v1 | 3 | [打开](<../cases/C14/review.md>) |
| C15 | v1 | 3 | [打开](<../cases/C15/review.md>) |
| C16 | d049 | 3 | [打开](<../cases/C16/review.md>) |
| C16 | d050 | 3 | [打开](<../cases/C16/review.md>) |
| C16 | d051_name | 3 | [打开](<../cases/C16/review.md>) |
| C16 | d051_no_name | 3 | [打开](<../cases/C16/review.md>) |
| C17 | v1 | 3 | [打开](<../cases/C17/review.md>) |
| C18 | v1 | 5 | [打开](<../cases/C18/review.md>) |
| C19 | same_amount | 3 | [打开](<../cases/C19/review.md>) |
| C19 | no_evidence | 3 | [打开](<../cases/C19/review.md>) |

## 逐文件快照

| 用例 / 变体 | 输入 PDF 与快照 | 正文粗分 → 原名合并 | 兜底金额 | 基础检查 | 角色草案对照 |
| --- | --- | --- | --- | --- | --- |
| C02 / v1 | [PDF](<../cases/C02/variants/v1/attachments/direct/无关凭证.pdf>) · [快照](<local_pdf/C02/v1/01.md>) | Invoice → Invoice | Not Found | 通过 | 一致 |
| C04 / v1 | [PDF](<../cases/C04/variants/v1/attachments/direct/办公用品发票.pdf>) · [快照](<local_pdf/C04/v1/01.md>) | Invoice → Invoice | Not Found | 通过 | 一致 |
| C05 / v1 | [PDF](<../cases/C05/variants/v1/attachments/direct/客运发票.pdf>) · [快照](<local_pdf/C05/v1/01.md>) | Invoice → Invoice | Not Found | 通过 | 一致 |
| C05 / v1 | [PDF](<../cases/C05/variants/v1/attachments/direct/行程单.pdf>) · [快照](<local_pdf/C05/v1/02.md>) | Itinerary → Itinerary | Not Found | 通过 | 一致 |
| C06 / v1 | [PDF](<../cases/C06/variants/v1/attachments/zip/压缩包内发票.pdf>) · [快照](<local_pdf/C06/v1/01.md>) | Invoice → Invoice | Not Found | 通过 | 一致 |
| C07 / v1 | [PDF](<../cases/C07/variants/v1/attachments/direct/发票甲.pdf>) · [快照](<local_pdf/C07/v1/01.md>) | Invoice → Invoice | Not Found | 通过 | 一致 |
| C07 / v1 | [PDF](<../cases/C07/variants/v1/attachments/zip/发票甲.pdf>) · [快照](<local_pdf/C07/v1/02.md>) | Invoice → Invoice | Not Found | 通过 | 一致 |
| C07 / v1 | [PDF](<../cases/C07/variants/v1/attachments/zip/发票乙.pdf>) · [快照](<local_pdf/C07/v1/03.md>) | Invoice → Invoice | Not Found | 通过 | 一致 |
| C08 / v1 | [PDF](<../cases/C08/variants/v1/attachments/direct/附件01.pdf>) · [快照](<local_pdf/C08/v1/01.md>) | Invoice → Invoice | Not Found | 通过 | 一致 |
| C09 / v1 | [PDF](<../cases/C09/variants/v1/attachments/direct/损坏发票.pdf>) · [快照](<local_pdf/C09/v1/01.md>) | Unknown → Invoice | Not Found | 通过 | 有差异，待审 |
| C11 / v1 | [PDF](<../cases/C11/variants/v1/attachments/direct/多金额多日期发票.pdf>) · [快照](<local_pdf/C11/v1/01.md>) | Itinerary → Invoice | Not Found | 通过 | 一致 |
| C12 / partial | [PDF](<../cases/C12/variants/partial/attachments/direct/缺票号发票.pdf>) · [快照](<local_pdf/C12/partial/01.md>) | Invoice → Invoice | Not Found | 通过 | 一致 |
| C12 / all_missing | [PDF](<../cases/C12/variants/all_missing/attachments/direct/三项全缺发票.pdf>) · [快照](<local_pdf/C12/all_missing/01.md>) | Invoice → Invoice | Not Found | 通过 | 一致 |
| C13 / v1 | [PDF](<../cases/C13/variants/v1/attachments/direct/交通发票.pdf>) · [快照](<local_pdf/C13/v1/01.md>) | Invoice → Invoice | Not Found | 通过 | 一致 |
| C14 / v1 | [PDF](<../cases/C14/variants/v1/attachments/direct/发票.pdf>) · [快照](<local_pdf/C14/v1/01.md>) | Invoice → Invoice | Not Found | 通过 | 一致 |
| C14 / v1 | [PDF](<../cases/C14/variants/v1/attachments/direct/行程单甲.pdf>) · [快照](<local_pdf/C14/v1/02.md>) | Itinerary → Itinerary | Not Found | 通过 | 一致 |
| C14 / v1 | [PDF](<../cases/C14/variants/v1/attachments/direct/行程单乙.pdf>) · [快照](<local_pdf/C14/v1/03.md>) | Itinerary → Itinerary | Not Found | 通过 | 一致 |
| C15 / v1 | [PDF](<../cases/C15/variants/v1/attachments/direct/发票B.pdf>) · [快照](<local_pdf/C15/v1/01.md>) | Invoice → Invoice | Not Found | 通过 | 一致 |
| C15 / v1 | [PDF](<../cases/C15/variants/v1/attachments/direct/行程单A.pdf>) · [快照](<local_pdf/C15/v1/02.md>) | Itinerary → Itinerary | Not Found | 通过 | 一致 |
| C15 / v1 | [PDF](<../cases/C15/variants/v1/attachments/direct/行程单B.pdf>) · [快照](<local_pdf/C15/v1/03.md>) | Itinerary → Itinerary | Not Found | 通过 | 一致 |
| C16 / d049 | [PDF](<../cases/C16/variants/d049/attachments/direct/发票C.pdf>) · [快照](<local_pdf/C16/d049/01.md>) | Invoice → Invoice | Not Found | 通过 | 一致 |
| C16 / d049 | [PDF](<../cases/C16/variants/d049/attachments/direct/行程单A.pdf>) · [快照](<local_pdf/C16/d049/02.md>) | Itinerary → Itinerary | Not Found | 通过 | 一致 |
| C16 / d049 | [PDF](<../cases/C16/variants/d049/attachments/direct/行程单D.pdf>) · [快照](<local_pdf/C16/d049/03.md>) | Itinerary → Itinerary | Not Found | 通过 | 一致 |
| C16 / d050 | [PDF](<../cases/C16/variants/d050/attachments/direct/发票C.pdf>) · [快照](<local_pdf/C16/d050/01.md>) | Invoice → Invoice | Not Found | 通过 | 一致 |
| C16 / d050 | [PDF](<../cases/C16/variants/d050/attachments/direct/行程单A.pdf>) · [快照](<local_pdf/C16/d050/02.md>) | Itinerary → Itinerary | Not Found | 通过 | 一致 |
| C16 / d050 | [PDF](<../cases/C16/variants/d050/attachments/direct/行程单D.pdf>) · [快照](<local_pdf/C16/d050/03.md>) | Itinerary → Itinerary | Not Found | 通过 | 一致 |
| C16 / d051_name | [PDF](<../cases/C16/variants/d051_name/attachments/direct/发票A.pdf>) · [快照](<local_pdf/C16/d051_name/01.md>) | Invoice → Invoice | Not Found | 通过 | 一致 |
| C16 / d051_name | [PDF](<../cases/C16/variants/d051_name/attachments/direct/行程单A.pdf>) · [快照](<local_pdf/C16/d051_name/02.md>) | Itinerary → Itinerary | Not Found | 通过 | 一致 |
| C16 / d051_name | [PDF](<../cases/C16/variants/d051_name/attachments/direct/行程单D.pdf>) · [快照](<local_pdf/C16/d051_name/03.md>) | Itinerary → Itinerary | Not Found | 通过 | 一致 |
| C16 / d051_no_name | [PDF](<../cases/C16/variants/d051_no_name/attachments/direct/发票C.pdf>) · [快照](<local_pdf/C16/d051_no_name/01.md>) | Invoice → Invoice | Not Found | 通过 | 一致 |
| C16 / d051_no_name | [PDF](<../cases/C16/variants/d051_no_name/attachments/direct/行程单A.pdf>) · [快照](<local_pdf/C16/d051_no_name/02.md>) | Itinerary → Itinerary | Not Found | 通过 | 一致 |
| C16 / d051_no_name | [PDF](<../cases/C16/variants/d051_no_name/attachments/direct/行程单D.pdf>) · [快照](<local_pdf/C16/d051_no_name/03.md>) | Itinerary → Itinerary | Not Found | 通过 | 一致 |
| C17 / v1 | [PDF](<../cases/C17/variants/v1/attachments/direct/发票A.pdf>) · [快照](<local_pdf/C17/v1/01.md>) | Invoice → Invoice | Not Found | 通过 | 一致 |
| C17 / v1 | [PDF](<../cases/C17/variants/v1/attachments/direct/行程单A.pdf>) · [快照](<local_pdf/C17/v1/02.md>) | Itinerary → Itinerary | Not Found | 通过 | 一致 |
| C17 / v1 | [PDF](<../cases/C17/variants/v1/attachments/direct/行程单B.pdf>) · [快照](<local_pdf/C17/v1/03.md>) | Itinerary → Itinerary | Not Found | 通过 | 一致 |
| C18 / v1 | [PDF](<../cases/C18/variants/v1/attachments/direct/发票A.pdf>) · [快照](<local_pdf/C18/v1/01.md>) | Invoice → Invoice | Not Found | 通过 | 一致 |
| C18 / v1 | [PDF](<../cases/C18/variants/v1/attachments/direct/发票B.pdf>) · [快照](<local_pdf/C18/v1/02.md>) | Invoice → Invoice | Not Found | 通过 | 一致 |
| C18 / v1 | [PDF](<../cases/C18/variants/v1/attachments/direct/发票C.pdf>) · [快照](<local_pdf/C18/v1/03.md>) | Invoice → Invoice | Not Found | 通过 | 一致 |
| C18 / v1 | [PDF](<../cases/C18/variants/v1/attachments/direct/行程单X.pdf>) · [快照](<local_pdf/C18/v1/04.md>) | Itinerary → Itinerary | Not Found | 通过 | 一致 |
| C18 / v1 | [PDF](<../cases/C18/variants/v1/attachments/direct/行程单C.pdf>) · [快照](<local_pdf/C18/v1/05.md>) | Itinerary → Itinerary | Not Found | 通过 | 一致 |
| C19 / same_amount | [PDF](<../cases/C19/variants/same_amount/attachments/direct/发票C.pdf>) · [快照](<local_pdf/C19/same_amount/01.md>) | Invoice → Invoice | Not Found | 通过 | 一致 |
| C19 / same_amount | [PDF](<../cases/C19/variants/same_amount/attachments/direct/行程单A.pdf>) · [快照](<local_pdf/C19/same_amount/02.md>) | Itinerary → Itinerary | Not Found | 通过 | 一致 |
| C19 / same_amount | [PDF](<../cases/C19/variants/same_amount/attachments/direct/行程单B.pdf>) · [快照](<local_pdf/C19/same_amount/03.md>) | Itinerary → Itinerary | Not Found | 通过 | 一致 |
| C19 / no_evidence | [PDF](<../cases/C19/variants/no_evidence/attachments/direct/发票C.pdf>) · [快照](<local_pdf/C19/no_evidence/01.md>) | Invoice → Invoice | Not Found | 通过 | 一致 |
| C19 / no_evidence | [PDF](<../cases/C19/variants/no_evidence/attachments/direct/行程单A.pdf>) · [快照](<local_pdf/C19/no_evidence/02.md>) | Itinerary → Itinerary | Not Found | 通过 | 一致 |
| C19 / no_evidence | [PDF](<../cases/C19/variants/no_evidence/attachments/direct/行程单B.pdf>) · [快照](<local_pdf/C19/no_evidence/03.md>) | Itinerary → Itinerary | Not Found | 通过 | 一致 |

基础检查只确认输入完整、预期可读性、真实本地链路运行及离线兜底到达。与 `extract.json` 的字段差异用于审核，不能直接判为兜底失败。
