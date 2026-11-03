# 发票邮件处理

从发票相关未读邮件中获取票据，提取并汇总报销字段，生成可下载的 ZIP。结果包含汇总表、处理记录和可交付的票据 PDF；请结合页面提示核对原件。产品验收已全部通过；具体测试范围和用例审核状态见[固定用例库](03%20tests/owner%20test/%E5%9B%BA%E5%AE%9A%E7%94%A8%E4%BE%8B%E5%BA%93/README.md)及测试记录。

## 使用

1. 在项目根目录安装 Python 依赖：`python -m pip install -r requirements.txt`。
2. 在 `.streamlit/secrets.toml` 或环境变量中配置百炼 API Key、文本模型和图片模型。以下型号为示例，实际可用性取决于账号、地域和服务端能力。

   千问（方案 C）：

   ```toml
   DASHSCOPE_API_KEY = "填写百炼 API Key"
   DASHSCOPE_MODEL = "qwen3.7-max"
   DASHSCOPE_VISION_MODEL = "qwen3-vl-plus"
   ```

   DeepSeek（方案 A）：

   ```toml
   DASHSCOPE_API_KEY = "填写百炼 API Key"
   DASHSCOPE_MODEL = "deepseek-v4.1-flash"
   DASHSCOPE_VISION_MODEL = "deepseek-v4.1-flash-1017"
   DEEPSEEK_BASE_URL = "https://实际业务空间ID.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
   ```

   DeepSeek 地址必须替换为自己业务空间及地域的实际百炼兼容接口地址。文本和图片模型必须属于同一已接入家族，但型号可以不同；通过配置检查不证明服务端支持该型号或图片输入。密钥环境变量优先于 secrets；模型配置优先读取 secrets。不要提交密钥。详见 [A 接入说明](A_%E6%8E%A5%E5%85%A5%E9%AA%8C%E8%AF%81%E8%AF%B4%E6%98%8E.md)和 [C 接入说明](C_%E6%8E%A5%E5%85%A5%E9%AA%8C%E8%AF%81%E8%AF%B4%E6%98%8E.md)。
3. 启动 `streamlit run app_demo.py`，选择邮箱服务器，输入账号和授权码。页面预设 163、QQ、126、Yeah，也可手动输入其他 IMAP 地址；先在邮箱服务商处启用 IMAP。
4. 处理结束后在页面下载本轮 ZIP，核对汇总表、票据和待核提示。程序不自动标记邮件已读，同一批未读邮件再次运行可能重复处理。

## 处理范围

程序筛选收件箱中主题符合发票相关关键词的未读邮件。有非 OFD 附件时处理附件，不检查正文链接；没有非 OFD 附件时检查正文链接。普通 ZIP 本身算附件，带文件名的内嵌部件也可能算附件。OFD 被忽略，不提取、不转换、不交付。

根目录 `ofd_processor.py` 暂时保留：当前生产主流程不调用它，但现行 OFD 忽略测试以动态名称引用该模块。主流程中用于识别并忽略 OFD 的判断仍然需要；保留该模块不表示产品支持 OFD 提取或转换。

PDF、XML 和图片进入各自处理路径；发票与行程单按规则匹配，字段缺失或关系不确定时提示核对。仅有 XML 而没有可交付票面时保留业务记录并提示缺凭证，不虚构 PDF。下载包包含 `发票信息汇总表.xlsx`、`task_debug.json` 和选定的票据 PDF。汇总表按发票输出八列：发票日期、发票号码、报销金额、发票类型、票面类型、出行时间、销售方、出发地/目的地。

提示词外置在项目根目录 [prompts.txt](prompts.txt)，由 `prompt_loader.py` 读取。程序不验证发票真伪，不执行报销审批；识别结果和票据仍需人工核对。

## 文档与验证

- [产品需求](01%20docs/01%20project/PRD.md)：业务目标、范围和规则。
- [主流程图](01%20docs/02%20technical-design/%E4%B8%BB%E6%B5%81%E7%A8%8B%E5%9B%BEmermaid.txt)：附件分支及业务流程。
- [固定用例库](03%20tests/owner%20test/%E5%9B%BA%E5%AE%9A%E7%94%A8%E4%BE%8B%E5%BA%93/README.md)：虚构用例、审核状态及受控运行边界。
- [逐字段预期草案](03%20tests/T3/%E5%86%BB%E7%BB%9318%E6%9D%A1%E9%80%90%E5%AD%97%E6%AE%B5%E6%89%A9%E5%B1%95_%E6%B5%8B%E8%AF%95%E5%B7%A5%E7%A8%8B%E5%B8%88%E8%8D%89%E6%A1%88.md)：扩展字段预期与审核状态。

产品验收通过不改变各测试的原始运行结果，也不代表待审核的逐字段预期已经审核。真实模型请求结果及字段差异见上述 A、C 接入说明；不要把少量样本的结果当成整体准确率。

已知限制：附件名称含“发票”但实际内容无关的场景曾出现报错，产品负责人接受暂不修复。文件名不能证明内容是发票，用户需核对原件及处理结果；产品验收已全部通过。
