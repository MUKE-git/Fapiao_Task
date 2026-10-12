# 合成发票字段审核表

本表列出各变体的合成发票号码、金额与开票日期。输入名称改为中性名称，测试场景只写在审核文档中。

票号为 20 位结构的合成测试号码，并非税务机关签发，不用于查验或报销。C07 的直接附件与 ZIP 内同一发票保持完全相同。

| 用例 | 变体 | 发票 PDF | 新金额 | 发票号码 | 开票日期 |
| --- | --- | --- | ---: | --- | --- |
| [C04](cases/C04/review.md) | a | [发票.pdf](cases/C04/variants/a/attachments/direct/%E5%8F%91%E7%A5%A8.pdf) | 116.56 | 26331202610040000123 | 2026-09-27 |
| [C04](cases/C04/review.md) | v1 | [发票.pdf](cases/C04/variants/v1/attachments/direct/%E5%8F%91%E7%A5%A8.pdf) | 304.48 | 26331988405429177621 | 2026-09-26 |
| [C05](cases/C05/review.md) | v1 | [发票.pdf](cases/C05/variants/v1/attachments/direct/%E5%8F%91%E7%A5%A8.pdf) | 38.93 | 26331148571660833404 | 2026-09-28 |
| [C06](cases/C06/review.md) | v1 | [发票.pdf](cases/C06/variants/v1/attachments/zip/%E5%8F%91%E7%A5%A8.pdf) | 82.97 | 26331350602504239155 | 2026-09-27 |
| [C07](cases/C07/review.md) | v1 | [发票甲.pdf](cases/C07/variants/v1/attachments/direct/%E5%8F%91%E7%A5%A8%E7%94%B2.pdf) | 70.91 | 26331358875592358614 | 2026-09-28 |
| [C07](cases/C07/review.md) | v1 | [发票乙.pdf](cases/C07/variants/v1/attachments/zip/%E5%8F%91%E7%A5%A8%E4%B9%99.pdf) | 190.69 | 26331783771999167861 | 2026-09-28 |
| [C08](cases/C08/review.md) | v1 | [附件01.pdf](cases/C08/variants/v1/attachments/direct/%E9%99%84%E4%BB%B601.pdf) | 244.94 | 26331229039876235290 | 2026-09-28 |
| [C11](cases/C11/review.md) | v1 | [发票.pdf](cases/C11/variants/v1/attachments/direct/%E5%8F%91%E7%A5%A8.pdf) | 134.62 | 26331243849362314463 | 2026-09-26 |
| [C12](cases/C12/review.md) | all_missing | [发票.pdf](cases/C12/variants/all_missing/attachments/direct/%E5%8F%91%E7%A5%A8.pdf) | Not Found | Not Found | Not Found |
| [C12](cases/C12/review.md) | partial | [发票.pdf](cases/C12/variants/partial/attachments/direct/%E5%8F%91%E7%A5%A8.pdf) | 262.68 | Not Found | 2026-09-27 |
| [C13](cases/C13/review.md) | v1 | [发票.pdf](cases/C13/variants/v1/attachments/direct/%E5%8F%91%E7%A5%A8.pdf) | 333.52 | 26331170435830407067 | 2026-09-27 |
| [C14](cases/C14/review.md) | v1 | [发票.pdf](cases/C14/variants/v1/attachments/direct/%E5%8F%91%E7%A5%A8.pdf) | 100.17 | 26331540109971408975 | 2026-09-27 |
| [C15](cases/C15/review.md) | v1 | [发票B.pdf](cases/C15/variants/v1/attachments/direct/%E5%8F%91%E7%A5%A8B.pdf) | 258.70 | 26331429265386194320 | 2026-09-28 |
| [C16](cases/C16/review.md) | d049 | [发票C.pdf](cases/C16/variants/d049/attachments/direct/%E5%8F%91%E7%A5%A8C.pdf) | 60.21 | 26331429192087625629 | 2026-09-28 |
| [C16](cases/C16/review.md) | d050 | [发票C.pdf](cases/C16/variants/d050/attachments/direct/%E5%8F%91%E7%A5%A8C.pdf) | 120.92 | 26331461668052080012 | 2026-09-28 |
| [C16](cases/C16/review.md) | d051_name | [发票A.pdf](cases/C16/variants/d051_name/attachments/direct/%E5%8F%91%E7%A5%A8A.pdf) | 138.69 | 26331626229746587002 | 2026-09-28 |
| [C16](cases/C16/review.md) | d051_no_name | [发票C.pdf](cases/C16/variants/d051_no_name/attachments/direct/%E5%8F%91%E7%A5%A8C.pdf) | 86.74 | 26331145455793714847 | 2026-09-27 |
| [C17](cases/C17/review.md) | v1 | [发票A.pdf](cases/C17/variants/v1/attachments/direct/%E5%8F%91%E7%A5%A8A.pdf) | Not Found | 26331882617152424518 | 2026-09-26 |
| [C18](cases/C18/review.md) | v1 | [发票A.pdf](cases/C18/variants/v1/attachments/direct/%E5%8F%91%E7%A5%A8A.pdf) | 98.31 | 26331456513317974903 | 2026-09-26 |
| [C18](cases/C18/review.md) | v1 | [发票B.pdf](cases/C18/variants/v1/attachments/direct/%E5%8F%91%E7%A5%A8B.pdf) | 98.47 | 26331989083246556926 | 2026-09-26 |
| [C18](cases/C18/review.md) | v1 | [发票C.pdf](cases/C18/variants/v1/attachments/direct/%E5%8F%91%E7%A5%A8C.pdf) | 107.74 | 26331585693637935814 | 2026-09-28 |
| [C19](cases/C19/review.md) | no_evidence | [发票C.pdf](cases/C19/variants/no_evidence/attachments/direct/%E5%8F%91%E7%A5%A8C.pdf) | 64.75 | 26331638222555199577 | 2026-09-26 |
| [C19](cases/C19/review.md) | same_amount | [发票C.pdf](cases/C19/variants/same_amount/attachments/direct/%E5%8F%91%E7%A5%A8C.pdf) | 263.18 | 26331289689000870570 | 2026-09-28 |
| [C22](cases/C22/review.md) | hit_miss_hit | [发票甲.pdf](cases/C22/variants/hit_miss_hit/attachments/direct/8221_%E5%8F%91%E7%A5%A8%E7%94%B2.pdf) | 355.29 | 26331780465373588813 | 2026-09-26 |
| [C22](cases/C22/review.md) | hit_miss_hit | [发票乙.pdf](cases/C22/variants/hit_miss_hit/attachments/direct/8223_%E5%8F%91%E7%A5%A8%E4%B9%99.pdf) | 289.87 | 26331751770250313413 | 2026-09-28 |
| [C22](cases/C22/review.md) | miss_then_hit | [发票甲.pdf](cases/C22/variants/miss_then_hit/attachments/direct/8212_%E5%8F%91%E7%A5%A8%E7%94%B2.pdf) | 97.49 | 26331461520399771704 | 2026-09-26 |

C12、C17 中故意缺失的字段仍写为 `Not Found`。C04 的 a/b 是交通类与非交通类；C11 现为可选历史样本。
