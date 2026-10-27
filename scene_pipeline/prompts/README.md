# 当前工程Prompt入口

所有阶段先使用[共享规则](00_shared.md)，再使用对应阶段Prompt。程序会拼接并自动提交，不需要操作者中途转发。

| 角色/阶段 | Prompt | 输出 |
|---|---|---|
| 分析 | [01_analysis](01_analysis.md) | 视频证据、部件、比例/连接约束、ROI和检查目标 |
| 参考生成与核对 | [02_reference](02_reference.md) | 实际参考图；核对角色按审查响应模板返回 |
| 灰模 | [03_blockout](03_blockout.md) | build(context)代码，建立几何、相机和环境 |
| 连接 | [04_connections](04_connections.md) | 关键节点与相对比例修正代码 |
| 材质 | [05_materials](05_materials.md) | ROI纹理、UV和材质代码及来源 |
| 视觉审查 | [06_review](06_review.md) | 每个实拍/模型目标的差异和问题单 |
| 修复 | [07_repair](07_repair.md) | 绑定当前模型的repair代码 |
| 数据 | [08_dataset](08_dataset.md) | 定版模型的同源数据规范，由程序执行 |

正常生产角色由project.json.models和roles决定。模型返回的代码必须包含build(context)，写入当前运行generated/；无法看图或解释结构时必须如实保留不确定项。程序会在上限内重试/返工，不会绕过质量门槛。
