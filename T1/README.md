# T1 旧版快照：发票邮件处理（Demo + 本机运营）

> 本目录是 Git 提交 `bfd6972`（`T1-7`）的只读行为基线，不参与新版运行。
> 新版代码位于项目根目录；从旧版借鉴逻辑时复制到新版后修改，不直接改动本目录。

## Streamlit 网页版（Demo）

1. 安装依赖：`pip install -r requirements.txt`
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

若你本机仍保留未被 Git 跟踪的 `history1_test.py`，也可：

```bash
streamlit run history1_test.py
```

或：

```bash
python history1_test.py
```

### 本机 vs 云端（临时目录）

- **本机默认**：结果写在**当前用户桌面**下你填写的子文件夹（如 `Invoice_Task`）。处理结束后可**下载 zip** 作备份。
- **Streamlit Community Cloud** 等环境：会自动使用**临时目录**，处理结束后请**下载 zip**；服务器不长期保存文件。也可在页面「高级选项」中勾选「强制使用临时目录并打包下载」在本机测试同样流程。
- 可选环境变量：`FAPIAO_USE_TEMP_OUTPUT=1` 时强制走临时目录 + zip。

页面与日志**不会**展示百炼 API Key。

### 输出内容

- **汇总表**：`发票信息汇总表.xlsx`，第一行为表头，列顺序为：发票日期、发票号码、报销金额、发票类型、票面类型、出行时间、销售方、邮件号码、行程类型、出发地/目的地。
- **调试**：`task_debug.json`。
- **附件与 PDF**：见目录 `1_Downloaded_Zips`、`2_Extracted_PDFs`。

### 命名规则

- 每个 PDF 的类型判定顺序为：**邮件附件原名**（含「行程单」「发票」等关键词）**>** **AI 的 file_classifications** **>** **PDF 正文关键词**。
- 仅当上述三种依据规范化后**不完全一致**时，输出文件名才带后缀 **「（待人工核查）」**；三者一致时不加该后缀。
- 若某封邮件出现 **Not Found**、**FIELD_NOT_FOUND** 或 **AI 调用异常**，Streamlit 页面会列出该邮件的**主题**与 **Date 头时间**，便于在邮箱里定位原信核对。

### 关于「附件越跑越多 / 和上次混在一起」

程序只处理**未读**邮件，且**不会**自动把邮件标为已读。若同一批未读被多次运行，或多次任务共用**同一输出文件夹**，`2_Extracted_PDFs` 里会累积多轮文件，看起来像「重复」。需要时可：手动将已处理邮件标为已读，或每次使用**新的输出文件夹名**，或清空该目录后再跑。

## 本机运营脚本

`main_test.py` 已列入 `.gitignore`，不进入远程仓库。使用前请设置环境变量：`IMAP_USER`（或 `IMAP_EMAIL`）、`IMAP_AUTH_CODE`、`DASHSCOPE_API_KEY`，可选 `IMAP_HOST`。

## 模块说明

- `invoice_pipeline.py`：收信、附件、AI、Excel、debug（无 Streamlit）；`zip_directory_to_bytes` 供打包下载。
- `app_demo.py`：Streamlit 界面
