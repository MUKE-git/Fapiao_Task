# 发票邮件处理（Demo + 本机运营）

## Streamlit 网页版（Demo）

1. 安装依赖：`pip install -r requirements.txt`
2. 配置 AI 密钥（二选一）  
   - **推荐**：在 `.streamlit` 文件夹里**新建**文件 `secrets.toml`（不必改 example 的文件名；若在资源管理器重命名后“又跳回去”，多半是云同步或复制成了带「副本」的文件名，直接在编辑器里新建 `secrets.toml` 即可），内容为：  
     `DASHSCOPE_API_KEY = "（百炼控制台复制的 API Key）"`  
   - 或设置环境变量 `DASHSCOPE_API_KEY`
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

在页面填写 IMAP 地址、邮箱、授权码；结果默认在**本机桌面**上指定文件夹（如 `Invoice_Task`）。页面与日志**不会**展示百炼 API Key。

## 本机运营脚本

`main_test.py` 已列入 `.gitignore`，不进入远程仓库。使用前请设置环境变量：`IMAP_USER`（或 `IMAP_EMAIL`）、`IMAP_AUTH_CODE`、`DASHSCOPE_API_KEY`，可选 `IMAP_HOST`。

## 模块说明

- `invoice_pipeline.py`：收信、附件、AI、Excel、debug（无 Streamlit）
- `app_demo.py`：Streamlit 界面
