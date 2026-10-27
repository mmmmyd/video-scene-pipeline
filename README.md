# 视频到Blender建模与多模态采集工程

**唯一开始入口：[START_HERE.md](START_HERE.md)。唯一配置：[project.json](project.json)。唯一运行命令：`python start.py`。**

先集中填写分析/建模模型、审查模型、参考图模型的服务信息与密钥环境变量，再填写视频、Blender及输出参数。配置完成后，程序自动抽帧、调用模型分析、生成参考图、核对参考、生成Blender代码、执行建模、检查、返工及导出，不需要逐阶段手工传JSON或写脚本。

```text
输入多视角视频 → 模型分析和证据/ROI → 参考图生成与核对
 → 模型生成灰模/连接/材质代码 → Blender MCP建模及检查点
 → 几何与实拍/模型视觉检查 → 自动修复和重检
 → 3D模型、RGB、Z深度、点云、位姿、多机位倒车视频、ZIP
```

核心目标：负向障碍物相关区域的纹理材质、关键部件连接、相对比例与升降方向。绝对尺寸允许估算，最终数据以定版模型为准。

已附带[三个块状水池测试视频](test_inputs/README.md)，默认配置直接引用这些输入。填写模型服务信息及密钥环境变量后即可运行。

## 使用

```powershell
python -m pip install -e .
# 编辑project.json，设置它指定的API密钥环境变量。
python start.py --check
python start.py
```

程序默认自行启动隐藏的Blender MCP工作进程，所需官方插件源码及许可证已随工程放在vendor/。无需提前打开Blender或启用GUI插件。也可将blender.backend设为cli，使用独立后台进程执行相同代码。

## 交接导航

- [START_HERE.md](START_HERE.md)：从配置模型到运行的一次性步骤。
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：平台接口、数据格式、质量约束和返工逻辑。
- [docs/HANDOFF.md](docs/HANDOFF.md)：给其他模型或开发者的接手规则。
- [scene_pipeline/prompts/README.md](scene_pipeline/prompts/README.md)：全部阶段Prompt。
- [validation/REPORT.md](validation/REPORT.md)：本机实际验证结果及证明范围。
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)：随附Blender插件的来源及许可证。

## 输出入口

完成后查看配置的output.directory：

```text
result.json            所有主要产物的实际路径
DELIVERY.zip           本次最终模型和数据交付包
launcher_state.json    全程状态、失败原因、自动返工记录
attempt_000/           当前项目第一次执行的任务、代码、检查点、图像和数据
attempt_001/           若本次需要返工，则保存下一轮；不覆盖失败证据
```

最终模型是可编辑.blend；GLB为便于复用的附加导出，程序材质未经烘焙时不保证外观等同Blender。每个机位输出逐帧RGB、每帧相机参数、按间隔采集的深度和相机/世界点云，以及MP4。

材质语义由配置的视觉模型负责审查；程序检查证据、逐项记录、几何和版本一致性。达到配置的重试/返工上限或存在无法解释的关键结构时，记录明确失败，不把未知项改成通过。

## 自检

```powershell
python -m unittest discover -s tests -v
python scripts/verify_full_pipeline.py --output outputs/software_check --backend managed_mcp --addon-dir vendor
```

集成自检使用明确标注的软件测试服务，真正运行Blender并验证自动返工和导出，不消耗真实模型API，也不代表真实视频材质还原质量。
