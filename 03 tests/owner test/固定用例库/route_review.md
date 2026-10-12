# 合成行程单起终点修订对照

按本次要求，将 C14–C19 共 10 个变体中的 20 份行程单示意地点替换为真实地点。C05 的地点已经具体，无需替换。

所有地点均在杭州市行政区域内，包括临安区和淳安县。起终点名称真实；行程、日期和金额仍是合成测试数据，金额用于原有匹配测试，不代表地图实时报价。

PDF、原始邮件内嵌附件、extract.json 的 origin_dest 和来源哈希、input_inventory.json 的大小及哈希已同步；生成器也已同步，并要求每条行程明确提供起终点。

已核对原有用例编号、顺序、金额、附件名、匹配预期及审核状态未改变。

## 地点核对

| 地点 | 所属城市及地址 | 地图 / 地址来源 |
| --- | --- | --- |
| 杭州东站 | 杭州市上城区天城路 1 号 | [高德地点](https://ditu.amap.com/place/B023B08WDR) |
| 杭州宋城 | 杭州市西湖区之江路 148 号 | [地图](https://m.map.360.cn/?new=1&pid=66dfcf8d8e2c2717&src=pc_shenbian) · [地址](https://you.ctrip.com/sight/hangzhou14/4081.html) |
| 杭州萧山国际机场 | 杭州市萧山区 | [地图地点](https://maps.apple.com/place?place-id=I79998E4AFC5A215E) |
| 千岛湖镇人民政府 | 杭州市淳安县环湖北路 645 号 | [百度地图查询](https://map.baidu.com/search/%E6%9D%AD%E5%B7%9E%E5%B8%82%E6%B7%B3%E5%AE%89%E5%8E%BF%E5%8D%83%E5%B2%9B%E6%B9%96%E9%95%87%E4%BA%BA%E6%B0%91%E6%94%BF%E5%BA%9C/) |
| 临安汽车东站 | 杭州市临安区农林大路与钱王街交汇处 | [高德地点](https://ditu.amap.com/place/B023B0A7RK) |

## 逐份对照

| 用例 / 变体 | 原示意路线 | 修改后路线 | PDF | 提取结果 |
| --- | --- | --- | --- | --- |
| C14 / v1 | 甲起点→甲终点 | 杭州东站→杭州宋城（之江路148号） | [行程单甲.pdf](<cases/C14/variants/v1/attachments/direct/行程单甲.pdf>) | [extract.json](<cases/C14/variants/v1/extract.json>) |
| C14 / v1 | 乙起点→乙终点 | 杭州宋城（之江路148号）→杭州东站 | [行程单乙.pdf](<cases/C14/variants/v1/attachments/direct/行程单乙.pdf>) | [extract.json](<cases/C14/variants/v1/extract.json>) |
| C15 / v1 | A起点→A终点 | 中国美术学院（南山校区）→杭州东站 | [行程单A.pdf](<cases/C15/variants/v1/attachments/direct/行程单A.pdf>) | [extract.json](<cases/C15/variants/v1/extract.json>) |
| C15 / v1 | B起点→B终点 | 浙江省科技馆（西湖文化广场2号）→杭州宋城 | [行程单B.pdf](<cases/C15/variants/v1/attachments/direct/行程单B.pdf>) | [extract.json](<cases/C15/variants/v1/extract.json>) |
| C16 / d049 | 目标起点→目标终点 | 杭州东站→杭州宋城 | [行程单A.pdf](<cases/C16/variants/d049/attachments/direct/行程单A.pdf>) | [extract.json](<cases/C16/variants/d049/extract.json>) |
| C16 / d049 | 干扰起点→干扰终点 | 杭州东站→杭州萧山国际机场 | [行程单D.pdf](<cases/C16/variants/d049/attachments/direct/行程单D.pdf>) | [extract.json](<cases/C16/variants/d049/extract.json>) |
| C16 / d050 | 目标起点→目标终点 | 杭州东站→杭州宋城 | [行程单A.pdf](<cases/C16/variants/d050/attachments/direct/行程单A.pdf>) | [extract.json](<cases/C16/variants/d050/extract.json>) |
| C16 / d050 | 干扰起点→干扰终点 | 杭州东站→杭州萧山国际机场 | [行程单D.pdf](<cases/C16/variants/d050/attachments/direct/行程单D.pdf>) | [extract.json](<cases/C16/variants/d050/extract.json>) |
| C16 / d051_name | 目标起点→目标终点 | 杭州东站→杭州宋城 | [行程单A.pdf](<cases/C16/variants/d051_name/attachments/direct/行程单A.pdf>) | [extract.json](<cases/C16/variants/d051_name/extract.json>) |
| C16 / d051_name | 干扰起点→干扰终点 | 杭州东站→杭州萧山国际机场 | [行程单D.pdf](<cases/C16/variants/d051_name/attachments/direct/行程单D.pdf>) | [extract.json](<cases/C16/variants/d051_name/extract.json>) |
| C16 / d051_no_name | 目标起点→目标终点 | 杭州东站→杭州宋城 | [行程单A.pdf](<cases/C16/variants/d051_no_name/attachments/direct/行程单A.pdf>) | [extract.json](<cases/C16/variants/d051_no_name/extract.json>) |
| C16 / d051_no_name | 干扰起点→干扰终点 | 杭州东站→杭州萧山国际机场 | [行程单D.pdf](<cases/C16/variants/d051_no_name/attachments/direct/行程单D.pdf>) | [extract.json](<cases/C16/variants/d051_no_name/extract.json>) |
| C17 / v1 | A起点→A终点 | 杭州东站→千岛湖镇人民政府 | [行程单A.pdf](<cases/C17/variants/v1/attachments/direct/行程单A.pdf>) | [extract.json](<cases/C17/variants/v1/extract.json>) |
| C17 / v1 | B起点→B终点 | 杭州萧山国际机场→千岛湖镇人民政府 | [行程单B.pdf](<cases/C17/variants/v1/attachments/direct/行程单B.pdf>) | [extract.json](<cases/C17/variants/v1/extract.json>) |
| C18 / v1 | X起点→X终点 | 杭州宋城→杭州萧山国际机场 | [行程单X.pdf](<cases/C18/variants/v1/attachments/direct/行程单X.pdf>) | [extract.json](<cases/C18/variants/v1/extract.json>) |
| C18 / v1 | C起点→C终点 | 临安汽车东站→杭州东站 | [行程单C.pdf](<cases/C18/variants/v1/attachments/direct/行程单C.pdf>) | [extract.json](<cases/C18/variants/v1/extract.json>) |
| C19 / same_amount | A起点→A终点 | 杭州萧山国际机场→杭州宋城 | [行程单A.pdf](<cases/C19/variants/same_amount/attachments/direct/行程单A.pdf>) | [extract.json](<cases/C19/variants/same_amount/extract.json>) |
| C19 / same_amount | B起点→B终点 | 杭州宋城→中国美术学院（南山校区） | [行程单B.pdf](<cases/C19/variants/same_amount/attachments/direct/行程单B.pdf>) | [extract.json](<cases/C19/variants/same_amount/extract.json>) |
| C19 / no_evidence | A起点→A终点 | 千岛湖镇人民政府→杭州东站 | [行程单A.pdf](<cases/C19/variants/no_evidence/attachments/direct/行程单A.pdf>) | [extract.json](<cases/C19/variants/no_evidence/extract.json>) |
| C19 / no_evidence | B起点→B终点 | 临安汽车东站→杭州萧山国际机场 | [行程单B.pdf](<cases/C19/variants/no_evidence/attachments/direct/行程单B.pdf>) | [extract.json](<cases/C19/variants/no_evidence/extract.json>) |

## 首次替换的历史验证结果（当前结果见下方）

- 20 份修改后的 PDF 已渲染并检查，起终点文字完整。
- 已核对邮件内嵌附件与落盘 PDF 字节一致，清单大小、哈希及提取结果来源哈希对应。
- [主流程及匹配用例](result/latest.md)：23/23 个变体通过；PDF 处理结果由 extract.json 预先提供。
- [项目本地 PDF 处理实测](result/local_pdf_latest.md)：46/46 份通过现有基础检查，其中 1 份是故意损坏的 PDF。此结果表示链路运行和基础一致性检查通过，不代表全部字段识别正确。
- 本地实测仍有 1 份角色差异、45 份字段对照差异，保留在报告中供审核；本次未更改识别代码或标准答案。

## 路线区分度修订与当前结果

本次定向修订 10 份 PDF；全库 21 份行程单现有 14 条不同路线。C16 四个边界变体保留同一组路线，以便隔离金额变量。其他用例增加不同方向、括号、校区名称及门牌数字，地点仍在杭州市内。

新增地点已核查：[中国美术学院南山校区](https://www.caa.edu.cn/xy/lxwm1/nsxq.htm)、[浙江省科技馆](https://www.zjstm.org/about/traffic.htm)。

- B 环节（项目 local_inspect_pdf）：21/21 份行程单完整读出预设地点文字。该检查不等于后续结构化地点字段提取正确。
- 地点传递：根据冻结的预期配对关系，检查 JSON 与 Excel 的地点；未匹配发票不得带入任何行程地点。全部通过。
- 检查有效性：故意把 JSON 地点换成错误候选、把 Excel 地点换成错误候选、给未匹配发票残留地点，三类错误均被检出。
- 主流程整体 22/23：C18 配对正确，但冻结预期提示词“被多张发票竞争”与当前实际“一对一关系冲突”不一致。禁用本次新增检查后仍出现同一差异。保留 expected.json，待审核。
- 本地 PDF 基础检查 46/46；原有 1 份角色差异和 45 份字段对照差异仍保留，不宣称字段全部识别正确。
- 原有用例编号、顺序、金额、附件名、匹配预期均保持不变；PDF、EML、提取结果、清单哈希及快照已同步。
