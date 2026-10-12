# 固定用例库：18 条必选场景、C11 可选

快速跳转：[48.31 元邮件快照](#mail-snapshots) · [合成发票字段审核表](fixture_values_review.md) · [18 条必选用例索引](#case-index) · [当前受控离线运行结果](result/latest.md) · [历史本地 PDF 实测（旧样本）](result/local_pdf_latest.md)

<a id="mail-snapshots"></a>
## 48.31 元邮件快照与本库对照

下表的 48.31 元邮件是**真实邮件的旧工作树排查记录**，仅供理解环节；它未复制进本合成用例库，也没有成为已审核的标准答案。`00`～`06` 是七个编号观察点，`07` 是解释文档；第 `04` 点有多份文件。

| 环节 | 48.31 元邮件：点击实际快照 | 该环节的输入 → 输出 | 本库中可点开的对应材料 |
| --- | --- | --- | --- |
| 00 原始邮件 | [RFC822 原件](<C:/Users/lenovo/.codex/worktrees/8423/fapiao3.0/local_mail_inspection/uid_1747702824_20261008_162303/00_raw.eml>) | IMAP 抓取 → 整封邮件字节，内含邮件头、正文和编码后的附件 | [C05 合成原始邮件](cases/C05/variants/v1/00_raw.eml) |
| 01 邮箱定位与邮件头 | [IMAP/邮件头快照](<C:/Users/lenovo/.codex/worktrees/8423/fapiao3.0/local_mail_inspection/uid_1747702824_20261008_162303/01_imap.json>) | IMAP 状态及原件头 → UID、抓取方式、当时 Flags、日期和主题；Flags 不属于 `.eml` 正文 | [C05 邮箱前置状态](cases/C05/variants/v1/imap_state.json) |
| 02 MIME 拆分 | [MIME 部件清单](<C:/Users/lenovo/.codex/worktrees/8423/fapiao3.0/local_mail_inspection/uid_1747702824_20261008_162303/02_mime.json>) | 原始邮件 → 容器、HTML 正文及 3 个附件部件 | 本库运行时解析 `00_raw.eml`；没有另存逐例 MIME 快照 |
| 03 附件解码 | [附件与哈希清单](<C:/Users/lenovo/.codex/worktrees/8423/fapiao3.0/local_mail_inspection/uid_1747702824_20261008_162303/03_attachments.json>)；[发票 PDF](<C:/Users/lenovo/.codex/worktrees/8423/fapiao3.0/local_mail_inspection/uid_1747702824_20261008_162303/attachments/part2_Attachment_2.pdf>)、[发票 OFD](<C:/Users/lenovo/.codex/worktrees/8423/fapiao3.0/local_mail_inspection/uid_1747702824_20261008_162303/attachments/part3_Attachment_3.ofd>)、[行程 PDF](<C:/Users/lenovo/.codex/worktrees/8423/fapiao3.0/local_mail_inspection/uid_1747702824_20261008_162303/attachments/part4_Attachment_4.pdf>) | MIME 附件部件 → 解码出的文件字节、名称、大小和哈希 | [C05 输入总清单](cases/C05/variants/v1/input_inventory.json)（第 1 项属 00 原始邮件，后 2 项属 03 附件）、[合成发票](cases/C05/variants/v1/attachments/direct/%E5%8F%91%E7%A5%A8.pdf)、[合成行程单](cases/C05/variants/v1/attachments/direct/行程单.pdf) |
| 04 PDF 预读 | [本地分类](<C:/Users/lenovo/.codex/worktrees/8423/fapiao3.0/local_mail_inspection/uid_1747702824_20261008_162303/04_local_classification.json>)；[发票文字](<C:/Users/lenovo/.codex/worktrees/8423/fapiao3.0/local_mail_inspection/uid_1747702824_20261008_162303/04_pdf_text_01.txt>)、[行程单文字](<C:/Users/lenovo/.codex/worktrees/8423/fapiao3.0/local_mail_inspection/uid_1747702824_20261008_162303/04_pdf_text_02.txt>) | PDF 字节 → 提取文字与本地角色判断 | [C05 受控逐文件结果](cases/C05/variants/v1/extract.json)位于其后续接口，**不等于真实预读输出** |
| 05 AI 请求 | [构造的 AI 输入](<C:/Users/lenovo/.codex/worktrees/8423/fapiao3.0/local_mail_inspection/uid_1747702824_20261008_162303/05_ai_user_input.txt>) | 第 04 步文字和类型 → 待发送提示；当时没有实际 AI 响应 | 离线用例不调用 AI；没有对应响应快照 |
| 06 本地兜底 | [兜底结果](<C:/Users/lenovo/.codex/worktrees/8423/fapiao3.0/local_mail_inspection/uid_1747702824_20261008_162303/06_local_fallback.json>) | 第 04 步文字 → 旧版本地规则结果；与第 05 步是两条分支 | [C05 `extract.json`](cases/C05/variants/v1/extract.json)是预设接口返回，**不是此兜底结果** |
| 最终预期 / 实际 | 48.31 元邮件尚无已审核标准答案，也未用当前项目完整运行 | 逐文件结果 → 匹配、Excel/JSON；分别比较“应该”和“实际” | [C05 预期草案](cases/C05/variants/v1/expected.json)；[离线实际结果](result/latest.json) |

[48.31 元邮件原解释](<C:/Users/lenovo/.codex/worktrees/8423/fapiao3.0/local_mail_inspection/uid_1747702824_20261008_162303/07_walkthrough.md>)包含更多背景。上述真实邮件链接指向本机旧工作树的私有检查目录；该目录若被清理，链接会失效。

C04 保留原 v1 路径并新增 a 类变体；C05 补完整打车票面。C11 保留编号与材料，移至可选；其余必选用例仍按原顺序审核。C10、C20、C21 暂不制作。
所有输入均为**合成样本**；全部人工标准答案为**待审核草案**，没有真实邮箱凭据或用户邮件。
原始输入、附件与 SHA-256、受控逐文件结果、标准答案、审核页分开保存；运行结果另存，不覆盖标准答案。

<a id="case-index"></a>
## 18 条必选用例索引

| 用例 | 场景 | 变体 | 任务 | 审核 |
| --- | --- | --- | --- | --- |
| [C01](cases/C01/review.md) | 无未读邮件 | v1 | T2-1 | 待审核 |
| [C02](cases/C02/review.md) | 主题不命中 | v1 | T2-1 | 待审核 |
| [C03](cases/C03/review.md) | 命中主题但无 PDF | v1 | T2-1 | 待审核 |
| [C04](cases/C04/review.md) | 普通发票字段提取：a/b 类 | v1（b 类）、a（a 类） | T2-1、T2-2 | 待审核 |
| [C05](cases/C05/review.md) | 单张发票与单份行程单同封 | v1 | T2-1、T2-3 | 待审核 |
| [C06](cases/C06/review.md) | ZIP 根目录 PDF | v1 | T2-1、T2-5 | 待审核 |
| [C07](cases/C07/review.md) | 多附件及重复文件 | v1 | T2-1 | 待审核 |
| [C08](cases/C08/review.md) | 无类型提示的附件名 | v1 | T2-1、T2-5 | 待审核 |
| [C09](cases/C09/review.md) | 不可读 PDF | v1 | T2-1 | 待审核 |
| [C12](cases/C12/review.md) | 关键字段缺失 | partial, all_missing | T2-1、T2-6 | 待审核 |
| [C13](cases/C13/review.md) | 交通类发票无行程单 | v1 | T2-3、T2-6 | 待审核 |
| [C14](cases/C14/review.md) | T2-3 金额层级 | v1 | T2-3 | 待审核 |
| [C15](cases/C15/review.md) | T2-3 同层原名消歧 | v1 | T2-3、T2-5 | 待审核 |
| [C16](cases/C16/review.md) | T2-3 容差边界与原名下一步 | d049, d050, d051_name, d051_no_name | T2-3 | 待审核 |
| [C17](cases/C17/review.md) | T2-3 缺金额时原名兜底 | v1 | T2-3 | 待审核 |
| [C18](cases/C18/review.md) | T2-3 多票竞争一单 | v1 | T2-3 | 待审核 |
| [C19](cases/C19/review.md) | T2-3 无法唯一匹配 | same_amount, no_evidence | T2-3 | 待审核 |
| [C22](cases/C22/review.md) | 多封未读邮件的主题筛选 | all_miss, miss_then_hit, hit_miss_hit | T2-1 | 待审核 |

## 可选用例

- [C11 金额或日期有歧义](cases/C11/review.md)：常规金额与日期取值已并入 C04/C05；保留编号和历史材料，暂不计入必选验收。

## 复现边界

- 单封邮件例的 `00_raw.eml` 和 `imap_state.json` 是固定输入；C22 由 `imap_state.json` 按 UID 指向多份 `.eml`。`extract.json` 是受控 PDF 接口返回草案。
- 主流程与 T2-3 可以在受控提取条件下验证；真实 PDF 分类与字段识别须单独运行并记录，不得由受控返回值冒充通过。
- PDF 页上印有‘合成测试’，票号、时间、金额和单位均为虚构；原件不用于报销。
- 审核时逐例检查输入、环节输出、异常、副作用和来源。审核通过后才计入覆盖率。
- `build_synthetic_cases.py` 仅用于首次生成；它拒绝覆盖 `cases/`，防止破坏人工审核结果。

## 离线复跑

在项目根目录运行（需 Python、`pdfplumber`、`openpyxl`；不连接邮箱或 AI）：

```powershell
python -B "03 tests/owner test/固定用例库/run_fixed_cases.py"
```

结果写入 [result/latest.md](result/latest.md) 和 `result/latest.json`，不会改动 `expected.json` 或审核页。当前报告运行 18 条必选用例的 26 个变体，包含 C04 新增的 a 类和 C22 的 3 个变体；C11 可选，不纳入这次默认运行。真实 AI 识别与人工标准答案仍待分别验证。

## 本地 PDF 阶段实测

现有合成 PDF 已是用例输入。以下命令实际运行 PDF 预读、正文与原名分类、本地正则兜底和单文件 `process_pdf`；只把 AI HTTP 响应模拟为失败，不连接真实邮箱或 AI：

```powershell
python -B "03 tests/owner test/固定用例库/run_local_pdf_stages.py"
```

从[逐文件实测索引](result/local_pdf_latest.md)可点开输入 PDF 和各环节快照；[机器结果](result/local_pdf_latest.json)保存全文及结构化输出。该运行不修改 18 条用例、`extract.json`、`expected.json` 或原受控运行结果。`extract.json` 是匹配环节的受控接口草案，**不是本地兜底的标准答案**；两者字段差异须逐项审核，不能据此宣称真实 AI 提取通过。

[合成行程单起终点修订对照](route_review.md)：20 份行程单的真实同城地点、地图链接和逐份 PDF 索引。

## 固定邮件到最终输出的本地整链路

同一入口支持真实 PDF 模式：

```powershell
python -B "03 tests/owner test/固定用例库/run_fixed_cases.py" --pdf-mode local
```

邮箱只返回固定 EML，项目实际执行附件解码、PDF 预读、分类、字段提取、匹配和 JSON/Excel 输出。`process_pdf` 不再被预填结果替换；AI 调用模拟失败，因此这里验证的是本地兜底整链路，不包含真实邮箱连接或真实 AI 服务。

[整链路报告](result/local_flow_latest.md)提供每个变体的预读、最终 JSON、Excel 链接；[机器报告](result/local_flow_latest.json)保留实际调用检查和与冻结草案的差异。`extract.json` 只用于输入校验及预期对照，不作为 PDF 处理结果喂给后续环节。原有草案是受控提取条件下的预期，仍待逐项审核，不自动改成实际值。

不加参数仍运行原受控模式；两种模式的报告分别保存。原 `local_pdf_latest.md` 仍是逐 PDF 环节报告。

## 样本数据版本

2026-10-12 已更新合成发票的金额、号码和开票日期，以及相应行程单、邮件、清单和预期。`result/latest.md` 为本次新样本的受控离线运行结果；`result/local_flow_latest.md` 及其逐例快照也基于本次样本。`result/local_pdf_latest.md` 与旧的逐 PDF 快照仍属于历史样本，不应与新输入逐字段比较。原始生成脚本 `build_synthetic_cases.py` 是首次建库记录，会拒绝覆盖现有用例；当前样本及字段审核表是审核依据。


## 输入名称原则

附件名和 PDF 内样本名不写‘缺票号’‘全缺’‘损坏’等预期结论；A/B/C 尾缀只在原名匹配用例中保留。C04/C05 的 `excel_field_checks` 列出 Excel 各列具体预期。

