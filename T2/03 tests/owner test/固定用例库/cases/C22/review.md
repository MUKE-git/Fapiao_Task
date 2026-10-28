# C22 多封未读邮件的主题筛选

- 优先级：必选
- 样本性质：合成；没有真实用户数据
- 审核状态：已审核通过
- 对应任务：T2-1
- 本例审核重点：逐封筛选和继续处理；邮件原件、附件、记录必须按 UID 对应。
- PDF 来源：会议通知复用 C02 的合成样本；普通发票分别使用本例独立生成的合成样本，并封装进各自邮件原件；各变体的票号和金额不同。

## 变体与材料

| 变体 | 模拟邮箱状态 | 邮件原件与附件 | 清单、受控提取草案、预期 |
| --- | --- | --- | --- |
| all_miss | [邮箱状态](variants/all_miss/imap_state.json) | 8201：[邮件原件](variants/all_miss/mail/01_8201.eml) · [附件](variants/all_miss/attachments/direct/8201_%E9%83%A8%E9%97%A8%E4%BC%9A%E8%AE%AE%E9%80%9A%E7%9F%A5.pdf)<br>8202：[邮件原件](variants/all_miss/mail/02_8202.eml) · [附件](variants/all_miss/attachments/direct/8202_%E9%83%A8%E9%97%A8%E4%BC%9A%E8%AE%AE%E9%80%9A%E7%9F%A5.pdf) | [输入清单](variants/all_miss/input_inventory.json) · [受控提取草案](variants/all_miss/extract.json) · [已审核预期](variants/all_miss/expected.json) |
| miss_then_hit | [邮箱状态](variants/miss_then_hit/imap_state.json) | 8211：[邮件原件](variants/miss_then_hit/mail/01_8211.eml) · [附件](variants/miss_then_hit/attachments/direct/8211_%E9%83%A8%E9%97%A8%E4%BC%9A%E8%AE%AE%E9%80%9A%E7%9F%A5.pdf)<br>8212：[邮件原件](variants/miss_then_hit/mail/02_8212.eml) · [附件](variants/miss_then_hit/attachments/direct/8212_%E5%8F%91%E7%A5%A8%E7%94%B2.pdf) | [输入清单](variants/miss_then_hit/input_inventory.json) · [受控提取草案](variants/miss_then_hit/extract.json) · [已审核预期](variants/miss_then_hit/expected.json) |
| hit_miss_hit | [邮箱状态](variants/hit_miss_hit/imap_state.json) | 8221：[邮件原件](variants/hit_miss_hit/mail/01_8221.eml) · [附件](variants/hit_miss_hit/attachments/direct/8221_%E5%8F%91%E7%A5%A8%E7%94%B2.pdf)<br>8222：[邮件原件](variants/hit_miss_hit/mail/02_8222.eml) · [附件](variants/hit_miss_hit/attachments/direct/8222_%E9%83%A8%E9%97%A8%E4%BC%9A%E8%AE%AE%E9%80%9A%E7%9F%A5.pdf)<br>8223：[邮件原件](variants/hit_miss_hit/mail/03_8223.eml) · [附件](variants/hit_miss_hit/attachments/direct/8223_%E5%8F%91%E7%A5%A8%E4%B9%99.pdf) | [输入清单](variants/hit_miss_hit/input_inventory.json) · [受控提取草案](variants/hit_miss_hit/extract.json) · [已审核预期](variants/hit_miss_hit/expected.json) |

## 逐条审核

1. 每个变体先看邮箱状态：`unseen_uids` 的顺序就是扫描顺序；`mails` 把每个 UID 关联到各自的邮件原件。邮件头解码后的主题应与状态中的主题一致。
2. 对照原始邮件和独立附件，确认文件名、PDF 字节、输入清单的大小和 SHA-256 一致。不同邮件的附件不能串到一起。
3. `all_miss`：两封都读邮件头，都不完整获取；零 PDF 调用、零记录、零 Excel 行。
4. `miss_then_hit`：第一封不命中，第二封命中；第二封必须继续处理并产出自己的发票记录。
5. `hit_miss_hit`：第一、三封命中；中间一封跳过后仍需处理第三封。两条记录分别归属各自的 UID，号码和金额也应对应原 PDF。
6. `extract.json` 只给应进入 PDF 环节的附件提供受控结果；它不是本次运行的输出。对照 `expected.json` 检查总数、逐 UID 的邮件头获取、整封获取、PDF 调用、记录归属和 Excel 行数。
7. 在下表填写审核意见；如需调整标准答案，写明变体、UID、字段和依据。

| 审核人 | 日期 | 结论（通过/退回/暂缓） | 依据与修改意见 |
| --- | --- | --- | --- |
| 用户 | 2026-10-12 | 已审核通过 | 用户确认本例纳入首批必选用例。 |
