# C12 关键字段缺失

- 优先级：必选
- 样本性质：合成；没有真实用户数据
- 审核状态：已审核通过
- 对应任务：T2-1、T2-6
- 本例审核重点：输入主题、附件名和 PDF 内样本名均不提示缺失项；根据票面和提取结果分别检查部分缺失与三项全缺的入表边界。

| 变体 | 邮箱状态 / 邮件原件 | 原始附件（ZIP 内文件也列出） | 输入清单 / 受控提取草案 | 已审核预期 |
| --- | --- | --- | --- | --- |
| all_missing | [模拟邮箱状态](variants/all_missing/imap_state.json) · [邮件原件](variants/all_missing/00_raw.eml) | [发票.pdf](variants/all_missing/attachments/direct/%E5%8F%91%E7%A5%A8.pdf) | [附件清单](variants/all_missing/input_inventory.json) · [受控提取草案](variants/all_missing/extract.json) | [已审核预期](variants/all_missing/expected.json) |
| partial | [模拟邮箱状态](variants/partial/imap_state.json) · [邮件原件](variants/partial/00_raw.eml) | [发票.pdf](variants/partial/attachments/direct/%E5%8F%91%E7%A5%A8.pdf) | [附件清单](variants/partial/input_inventory.json) · [受控提取草案](variants/partial/extract.json) | [已审核预期](variants/partial/expected.json) |

> 受控提取草案是用例随附的固定字段输入，供相应阶段测试使用；它不是程序本次运行产生的结果。已审核预期记录用户确认的标准答案；测试运行结果另行核对。运行报告应单独保存。

## 逐条审核

1. 按上表逐个打开邮箱状态、邮件原件和附件。邮箱状态说明模拟邮箱当前有哪些未读邮件；邮件原件是 RFC 822/MIME 邮件文件；input_inventory 清单记录邮件及附件的路径、来源、字节数和 SHA-256。核对清单指向的文件确实存在且对应。
2. 本例审核重点：分别审核 partial 和 all_missing，不能把两个变体混为一谈。partial 只缺发票号码，金额仍为 262.68，预期保留记录并写入一行；all_missing 的号码、日期、金额全缺，预期保留 JSON 记录但不写 Excel 行，并出现缺失提示。
3. 对照对应变体的受控提取草案与已审核预期。核实预期里的数量、关联关系、提示和字段值是否符合本例输入及业务规则；不要把草案当成已运行结果，也不要覆盖它。需要审核真实运行结果时，应查看单独保存的运行报告。
4. 在下表填写审核意见。若认为输入或预期需要修改，写明文件、具体字段、修改理由；不要只写有问题。

| 审核人 | 日期 | 结论（通过/退回/暂缓） | 依据与修改意见 |
| --- | --- | --- | --- |
| 用户 | 2026-10-12 | 已审核通过 | 用户确认本例纳入首批必选用例。 |
