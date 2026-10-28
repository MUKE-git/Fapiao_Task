# C16 T2-3 容差边界与原名下一步

- 优先级：必选
- 样本性质：合成；没有真实用户数据
- 审核状态：已审核通过
- 对应任务：T2-3
- 本例审核重点：0.49/0.50 进入容差层；0.51 不入该层，但唯一同尾缀可兜底。

| 变体 | 邮箱状态 / 邮件原件 | 原始附件（ZIP 内文件也列出） | 输入清单 / 受控提取草案 | 已审核预期 |
| --- | --- | --- | --- | --- |
| d049 | [模拟邮箱状态](variants/d049/imap_state.json) · [邮件原件](variants/d049/00_raw.eml) | [发票C.pdf](variants/d049/attachments/direct/%E5%8F%91%E7%A5%A8C.pdf) · [行程单A.pdf](variants/d049/attachments/direct/%E8%A1%8C%E7%A8%8B%E5%8D%95A.pdf) · [行程单D.pdf](variants/d049/attachments/direct/%E8%A1%8C%E7%A8%8B%E5%8D%95D.pdf) | [附件清单](variants/d049/input_inventory.json) · [受控提取草案](variants/d049/extract.json) | [已审核预期](variants/d049/expected.json) |
| d050 | [模拟邮箱状态](variants/d050/imap_state.json) · [邮件原件](variants/d050/00_raw.eml) | [发票C.pdf](variants/d050/attachments/direct/%E5%8F%91%E7%A5%A8C.pdf) · [行程单A.pdf](variants/d050/attachments/direct/%E8%A1%8C%E7%A8%8B%E5%8D%95A.pdf) · [行程单D.pdf](variants/d050/attachments/direct/%E8%A1%8C%E7%A8%8B%E5%8D%95D.pdf) | [附件清单](variants/d050/input_inventory.json) · [受控提取草案](variants/d050/extract.json) | [已审核预期](variants/d050/expected.json) |
| d051_name | [模拟邮箱状态](variants/d051_name/imap_state.json) · [邮件原件](variants/d051_name/00_raw.eml) | [发票A.pdf](variants/d051_name/attachments/direct/%E5%8F%91%E7%A5%A8A.pdf) · [行程单A.pdf](variants/d051_name/attachments/direct/%E8%A1%8C%E7%A8%8B%E5%8D%95A.pdf) · [行程单D.pdf](variants/d051_name/attachments/direct/%E8%A1%8C%E7%A8%8B%E5%8D%95D.pdf) | [附件清单](variants/d051_name/input_inventory.json) · [受控提取草案](variants/d051_name/extract.json) | [已审核预期](variants/d051_name/expected.json) |
| d051_no_name | [模拟邮箱状态](variants/d051_no_name/imap_state.json) · [邮件原件](variants/d051_no_name/00_raw.eml) | [发票C.pdf](variants/d051_no_name/attachments/direct/%E5%8F%91%E7%A5%A8C.pdf) · [行程单A.pdf](variants/d051_no_name/attachments/direct/%E8%A1%8C%E7%A8%8B%E5%8D%95A.pdf) · [行程单D.pdf](variants/d051_no_name/attachments/direct/%E8%A1%8C%E7%A8%8B%E5%8D%95D.pdf) | [附件清单](variants/d051_no_name/input_inventory.json) · [受控提取草案](variants/d051_no_name/extract.json) | [已审核预期](variants/d051_no_name/expected.json) |

> 受控提取草案是用例随附的固定字段输入，供相应阶段测试使用；它不是程序本次运行产生的结果。已审核预期记录用户确认的标准答案；测试运行结果另行核对。运行报告应单独保存。

## 逐条审核

1. 按上表逐个打开邮箱状态、邮件原件和附件。邮箱状态说明模拟邮箱当前有哪些未读邮件；邮件原件是 RFC 822/MIME 邮件文件；input_inventory 清单记录邮件及附件的路径、来源、字节数和 SHA-256。核对清单指向的文件确实存在且对应。
2. 本例审核重点：按变体逐行审核金额容差边界和原名兜底。d049（差 0.49）与 d050（差 0.50）应由容差层匹配 A；两个 d051 变体中，行程单 A 与发票差 0.51、行程单 D 差 0.52；两单金额都接近，但都超出 0.50 容差。d051_name 只有 A 与发票同后缀，应由原名层匹配 A；d051_no_name 两单都无同后缀，不应自动匹配。逐个核对对应的两个行程单和 expected.json。
3. 对照对应变体的受控提取草案与已审核预期。核实预期里的数量、关联关系、提示和字段值是否符合本例输入及业务规则；不要把草案当成已运行结果，也不要覆盖它。需要审核真实运行结果时，应查看单独保存的运行报告。
4. 在下表填写审核意见。若认为输入或预期需要修改，写明文件、具体字段、修改理由；不要只写有问题。

| 审核人 | 日期 | 结论（通过/退回/暂缓） | 依据与修改意见 |
| --- | --- | --- | --- |
| 用户 | 2026-10-12 | 已审核通过 | 用户确认本例纳入首批必选用例。 |
