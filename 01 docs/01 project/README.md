# 发票邮件处理（Demo + 本机运营）

> 2026-10-01：新版入口为 app_demo.py → pipeline.py。以下业务说明按确认后的需求更新；完整行程单 JSON、导出命名、Decimal、字段校验和日志位置等仍需代码适配，详见《计划分工.md》。

## Streamlit 网页版（Demo）

1. 在项目根目录准备运行环境；当前项目未提供 requirements.txt，依赖清单仍需补齐。
2. 配置 AI 密钥（二选一）  
   - **推荐**：在 `.streamlit` 文件夹里**新建**文件 `secrets.toml`（不必改 example 的文件名；若在资源管理器重命名后"又跳回去"，多半是云同步或复制成了带「副本」的文件名，直接在编辑器里新建 `secrets.toml` 即可），内容为：  
     ```
     DASHSCOPE_API_KEY = "（百炼控制台复制的 API Key）"
     DASHSCOPE_MODEL = "qwen3.7-plus"
     ```  
   - 或设置环境变量 `DASHSCOPE_API_KEY` 和 `DASHSCOPE_MODEL`
3. 启动：

```bash
streamlit run app_demo.py
```

### 本机 vs 云端（临时目录）

- **本机默认**：结果写在**当前用户桌面**下你填写的子文件夹（如 `Invoice_Task`）。处理结束后可**下载 zip** 作备份。
- **Streamlit Community Cloud** 等环境：会自动使用**临时目录**，处理结束后请**下载 zip**；服务器不长期保存文件。也可在页面「高级选项」中勾选「强制使用临时目录并打包下载」在本机测试同样流程。
- 可选环境变量：`FAPIAO_USE_TEMP_OUTPUT=1` 时强制走临时目录 + zip。

页面与日志**不会**展示百炼 API Key。

### 输出内容

- **汇总表**：`发票信息汇总表.xlsx`，第一行为表头，列顺序为：发票日期、发票号码、报销金额、发票类型、票面类型、出行时间、销售方、出发地/目的地。
- **调试**：task_debug.json，目标结构保存每张发票与全部行程单四字段（编号、匹配金额、出行时间、起终点），包括未匹配项及内部/导出名称对应关系。
- **入表**：按发票判断，票号、日期、金额全部缺失才排除并前端提醒；只缺部分仍入表并核查。发票类型可空，票面类型必填并用于 a/b 判定；行程单只汇入旅行两列。
- **运行日志**：统一目标位置为项目 04 logs/；测试日志仍在对应 result/。
- **附件与 PDF**：见目录 `1_Downloaded_Zips`、`2_Extracted_PDFs`。

### 命名规则（导出适配待实现）
- 内部保留 Msg{序号}_{文件序号}_{安全名}；用户压缩包导出副本按“票面类型＋日期＋金额元”命名，行程单按“行程单＋出行日期＋匹配金额元”命名，未知按“票据＋日期＋金额元”。扩展名保留实际格式。
- JSON 原名完整保留；实际存盘名最多 120 字符（含编号、扩展名、重名后缀），超长主体截断加短摘要，重名加序号。
- 每个 PDF 的类型由「预读类型 + AI file_classifications」判定；附件名含「行程单」关键词时优先判为行程单。
- 若某封邮件出现 **Not Found**、**FIELD_NOT_FOUND** 或 **AI 调用异常**，Streamlit 页面会列出该邮件的**主题**与 **Date 头时间**，便于在邮箱里定位原信核对。

### 关于「附件越跑越多 / 和上次混在一起」

程序只处理**未读**邮件，且**不会**自动把邮件标为已读。若同一批未读被多次运行，或多次任务共用**同一输出文件夹**，`2_Extracted_PDFs` 里会累积多轮文件，看起来像「重复」。需要时可：手动将已处理邮件标为已读，或每次使用**新的输出文件夹名**，或清空该目录后再跑。

## 模块说明

- app_demo.py：页面入口，调用新版 pipeline.run_pipeline；后续按 T3-1 适配提示和用户打包。
- pipeline.py：收件、调度、记录组装，统一调用 Excel 与 JSON 输出。
- attachment_processor.py / pdf_processor.py：附件登记与单文件 PDF 处理。
- data_model.py / processor_contracts.py / stage_ports.py：数据结构、处理器返回结果、后续阶段入口。
- ai_client.py / fallback_extractor.py / type_classifier.py：AI、正则和分类能力；config.py / utils.py 为配置与工具。
- excel_writer.py：写表；invoice_pipeline.py：兼容导入过渡层。
- T1/ 为旧版快照；未来新增文件位置见《项目目录与文件归档规则.md》。

编号仅本轮唯一；每轮选择不同输出目录。模型由配置选择，提示词当前内置；模型接口改造纳入 T3-2。
