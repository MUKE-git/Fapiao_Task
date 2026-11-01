# 方案 C 接入与验证（2026-11-01）

本文件记录方案 C 完成时的验证。随后方案 A 已新增独立实现，当前开发和在线验证状态见《A_接入验证说明.md》；C 的调用实现保留。

## 使用

在 `.streamlit/secrets.toml` 明确填写 `DASHSCOPE_MODEL="qwen3.7-max"`、`DASHSCOPE_VISION_MODEL="qwen3-vl-plus"` 和 API Key。页面模型名仍按 secrets、环境变量的顺序读取，缺少配置不再使用旧模型默认值。Key 保留现有环境变量优先、secrets 次之的安全读取方式。

后续用户变更：以上型号改为示例，不再固定两套精确组合。支持同家族型号分别配置，实际请求原样使用配置名称；不同家族混用、空值、非法格式和未接入家族继续报错。服务端验证具体型号可用性和输入能力，不自动切换。

`prompts.txt` 包含四个分区。保留分区标记，编辑正文后保存；下一次调用重新读取。票据原文、图片、文件标识和仲裁候选由程序动态传入。初始四段正文已与基线提交中的内置提示词逐字比较，一致。不存在、缺失、空白的分区不能静默使用旧提示词。

图片、PDF 文本和去重仲裁通过 `model_gateway.qwen_call` 使用千问 SDK。文本与图片分别调用 Generation 和 MultiModalConversation；仲裁显式使用本轮 Key。旧临时副本及重命名逻辑保留。现有同模型重试仍存在，不会切换模型；在线验证脚本另设单请求限额以控制费用。

`04 logs/model_calls.jsonl` 记录请求开始、响应状态及处理结果；文件级业务处理结果还保留在原调试结构中。日志不记录 Key、票据原文或完整服务错误消息。日志中的 `response_received` 只代表接口成功返回，不能证明字段正确；`processing_result` 记录后续解析/仲裁结论。

## 证据

- Python：`D:\Anaconda\envs\fapiao\python.exe`。
- `test_model_c.py`：5 项通过（热读取、无效分区、错误组合、SDK 路由、失败不切换）。
- `02 build/T3-5/test_image_processor.py`：17 项通过。
- `02 build/T2-1/test_pdf_batch_repair.py`：10 项通过；受控 SDK 测试需设置非真实占位 Key，未更改预期。
- `02 build/T2-4/test_dedup_arbitrator.py`：13/14。剩余 `test_run_pipeline_writes_one_excel_row_but_both_json_sources` 在 HEAD 基线复现同样失败；它期待的重复 Excel 行提示未出现。本任务未修改该业务规则或冻结预期。
- 修改的 Python 文件通过编译检查。

`c_online_evidence.json` 保存两次成功在线调用的响应、状态和独立指定的预期比较：C 文本（旧 PDF 提取入口）一次、C 图片（单文件图片入口）一次。使用合成英文票据；号码、日期、金额、销售方、项目名称五项均一致。它证明接口和该样本通过，不等于真实票据准确率验收；真实仲裁未另行调用。

首次两个在线尝试因沙箱 DNS 错误未收到响应；网络权限重试后两次成功。按保守口径四次额度已用完，方案 A 的真实调用需要追加授权。`verify_c_online.py --run-two-requests` 会消耗请求额度，不应无意重跑。
