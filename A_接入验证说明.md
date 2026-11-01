# 方案 A 开发与验证

方案 A 使用配置中的 DeepSeek 型号。按用户后续要求，取消固定型号组合限制；文本可填 `deepseek-v4.1-flash`，图片可填 `deepseek-v4.1-flash-1017`。`model_gateway.deepseek_call` 使用 OpenAI Python SDK 访问百炼的 OpenAI 兼容接口。图片消息转换为 `image_url`，文本消息保留；响应转换成现有业务处理器使用的统一形式。

PDF 文本、单文件图片、去重仲裁均经过 `model_call` 根据模型家族选择独立实现，具体型号原样发送。页面和主流程检查名称非空、格式正确、家族已实现且两类模型同家族；同家族允许型号不同，不引入 AI_PLAN 配置。前缀识别不证明模型存在或支持图片，具体权限与能力以服务响应为准，不会自动更换失败型号。SDK 自动重试关闭，原业务层的同模型重试继续保留。

## 配置

安装 requirements.txt，在 `.streamlit/secrets.toml` 填写：

```toml
DASHSCOPE_MODEL = "deepseek-v4.1-flash-1017"
DASHSCOPE_VISION_MODEL = "deepseek-v4.1-flash-1017"
DASHSCOPE_API_KEY = ""
DEEPSEEK_BASE_URL = "https://实际业务空间ID.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
```

地址必须替换成自己的真实业务空间、地域对应的地址，不能直接复制占位符。Key 继续按现有安全配置读取；不需要 OpenAI 平台的 Key。C 不需要新增地址。地域地址依据[百炼官方接口说明](https://help.aliyun.com/zh/model-studio/deepseek-api)。

## 验证边界

`test_model_a.py` 为离线受控 SDK 测试，检查 A/C 组合、地址、文本和图片请求转换、共同结果解析、旧 PDF 入口及失败不切换；不能证明服务端实际接受该模型或账号配置。

实际运行：A 专项 6 项、C 专项 5 项通过；图片回归 17 项、PDF 回归 10 项通过。Python 编译检查与 git diff --check 通过。fapiao 环境已安装 openai 2.54.0，requirements.txt 固定该已验证版本；pip check 无损坏依赖。

用户追加 3 次额度并配置地址后，A 的文本、图片真实请求均成功返回。证据见 `a_online_evidence.json`：合成英文样本的文本 5/5 字段一致；图片号码、日期、金额、销售方 4 项一致，票面类型预期 Office supplies、实际 Not Found，保留为差异，不改预期。当前图片提示词要求从票面“项目名称”提取，本样本为英文 Item；这可能有关，但尚不能认定为唯一原因。接口成功不等于字段全部正确，也不等于真实发票业务验收通过。

本轮补回本地缺失的 DASHSCOPE_MODEL 配置；首次初始化发现 SOCKS 代理缺少 socksio，未发送服务请求。已安装 socksio==1.0.0 并写入依赖。之后实际请求 2 次，追加额度剩余 1 次；没有自动切换模型。A、C 专项 11 项再次通过，pip check 通过。

用户重新配置 TOML 后又授权执行剩余一次图片复测：接口成功、解析成功，仍为 4/5 字段一致，票面类型仍返回 Not Found。证据为 `a_image_retest_evidence.json`，原先证据保留。追加的 3 次额度至此全部使用，不再自动发起请求。

随后用户单独追加授权 1 次文本复测：使用当前 TOML 的 deepseek-v4.1-flash-1017，实际请求 1 次，成功响应且无处理错误；号码、日期、金额、销售方、票面类型五项与独立预期一致。证据为 `a_text_retest_evidence.json`。该单次追加授权已使用完毕。

取消精确型号组合限制后，用户另外授权文本、图片各一次实测：文本实际请求 deepseek-v4.1-flash，图片实际请求 deepseek-v4.1-flash-1017，均成功响应且解析成功。文本五项字段一致；图片四项一致，票面类型仍为 Not Found。证据为 `a_configured_models_live_evidence_20261101_204952.json`。本轮实际请求两次，无额外重试，无型号切换，未修改 TOML。

`verify_models_online.py --run-text-once` 或 `--run-image-once` 分别执行至多一次请求；`--run-two-requests` 执行两类。均按 secrets 中的两个模型名选择，不应无意重跑。
