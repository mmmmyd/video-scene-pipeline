# 给接手模型或开发者

先读START_HERE.md和project.json。公开入口只有start.py；不要要求操作者中途手工生成建模代码、填写审查文件或逐步执行命令。

需要替换模型时修改models/roles；需要其他API时增加providers传输或使用command/json_http适配器。任何厂商的模型都遵守相同任务协议，不依赖聊天历史、GPT SDK或固定工具名称。

模块职责：

| 文件 | 职责 |
|---|---|
| entry.py / launcher.py | 入口、前置校验、全程调度、自动修复、结果索引 |
| configuration.py | 自动生成内部运行配置 |
| agent_tasks.py / prompts/ | 分阶段任务、Prompt与响应模板 |
| providers.py | 实际模型请求、图片传输、凭据隔离、响应解析 |
| automatic.py | 代码落盘、参考审查、修复代码 |
| blender_bridge.py | CLI/MCP调用和工作进程生命周期 |
| blender_runtime.py | Blender建模、检查点、四视角、audit与冻结数据 |
| accuracy.py | 证据、ROI、约束、逐项审查、版本门槛 |
| media.py / geometry.py | 视频、点云、坐标和编码 |
| workflow.py / revisions.py | 阶段执行与不可覆盖的修复运行 |

原始视频和模型素材不随代码包内置，均由本次project.json输入。vendor仅包含实际需要的官方Blender插件及许可证，不包含整个旧工作区、历史case、旧运行或旧版本包。

继续开发时运行python -m unittest discover -s tests -v；完整集成用scripts/verify_full_pipeline.py，可分别选择cli和managed_mcp。tests/fixture_provider.py是确定性软件测试响应器，不是大模型、也不能代表实拍重建质量。

不要因为输出文件存在就判定建模通过。保持证据链、各部件连接/相对比例、ROI材质及检查门槛。遇到模型无法解释的关键结构必须保留失败，不用自动放宽范围来凑通过。
