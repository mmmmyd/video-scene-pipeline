# 从这里开始

本工程只有一个用户入口：**配置project.json，然后执行python start.py**。请先完成大模型配置，程序后续会自动调用，不需要你在中途切换聊天或手工生成建模脚本。

## 1. 首先配置大模型

打开项目根目录project.json，它的第一部分就是models：

| 配置 | 职责 | 必须具备的能力 |
|---|---|---|
| models.builder | 视频帧分析、空间解释、灰模/连接/材质代码、自动返工 | 看图、生成Python/JSON |
| models.reviewer | 核对参考图、对比实拍与模型、逐项判定和问题单 | 看图、结构化JSON |
| models.reference | 由实拍图生成一致的多视角辅助参考 | 图像生成/编辑 |

每个模型填写：provider、model、transport、endpoint、api_key_env。模型名使用服务实际开放的ID；image2.5是本流程参考图阶段的名称，实际API模型ID由你填写。builder和reviewer可以使用同一个模型，在roles中指向同一配置即可。

支持四种transport：

- chat_completions：兼容Chat Completions协议的视觉/代码模型，endpoint填写完整的/chat/completions地址。
- image_edits：兼容/images/edits的参考图服务，程序发送实拍联系表的multipart图片，读取base64生成图。
- json_http：你自己的服务适配器，接收task/context/images并返回任务协议JSON。
- command：任意厂商SDK或本地模型命令行适配器，参数中包含{request}/{response}；{python}指当前Python。

不要把真实密钥写入project.json。PowerShell中设置配置所指向的环境变量，例如：

```powershell
$env:SCENE_MODEL_API_KEY = '填写你的分析及审查服务密钥'
$env:SCENE_IMAGE_API_KEY = '填写你的参考图服务密钥'
```

不同服务使用不同密钥时，为相应profile分别填写api_key_env。程序不会输出密钥值，交接包也不包含密钥。

## 2. 配置输入、Blender和输出

在同一个project.json中填写：

工程已附带三个块状水池测试视频，默认input.videos已经指向test_inputs/pool/，可直接保留这些路径。更换场景时再修改输入列表。说明见[test_inputs/README.md](test_inputs/README.md)。

- input.videos：全部源视频路径，支持中文路径。相对路径以project.json所在目录为基准。
- blender.executable：Blender可执行文件；本机可填blender或明确的绝对路径。
- blender.backend：默认managed_mcp，程序自动启动和关闭自己的MCP工作进程；cli为后台脚本模式。
- blender.addon_dir：默认vendor，已随工程提供插件，不需要另行下载。
- output.directory：本次输出目录。修改已开始运行的任务配置时使用新目录。
- output.view_ids：需要的机位名称；模型会创建对应DATA_相机。
- output.duration_s/fps：每个机位视频时长和帧率。
- output.multimodal_every：每隔几帧采集深度及点云；设1为每帧采集，末帧总会采集。
- output.width/height/samples：分辨率和Blender采样；宽高需为偶数。
- limits：服务重试次数、格式校验重试次数、最多自动返工轮数。

默认每个机位3秒、24fps、640×360。可按需要增加机位、时长和分辨率。

policy.video_coverage默认sampled_frames：输入仍是原视频，HTTP视觉模型实际收到按ingest设置提取的帧，记录中不会声称完整看过原视频。若要完整视频理解，选择full_video并配置具备video能力的command适配器。关键帧不足导致结构不确定时，流程会保留问题而非编造。

## 3. 一次安装依赖，随后直接运行

要求Python 3.10+和Blender。本机已验证Blender 5.2.2 LTS。

```powershell
cd '工程所在目录'
python -m pip install -e .
python start.py --check
python start.py
```

--check只检查配置、密钥变量是否存在、视频可解码性和本地依赖，不调用付费服务，也不能证明远端账号权限。真正运行时由实际API响应决定模型是否可用。

## 4. 后续程序自动完成

抽帧 → 分析证据/部件/材质ROI → 参考图生成和核对 → 三批建模代码生成 → Blender建模 → 四视角渲染及几何检查 → 模型逐项视觉审查 → 必要时自动写修复代码并重做 → 多模态数据和倒车视频 → 验证打包。

不需要逐阶段填写JSON、不需要手工执行revise，也不需要保持本聊天打开。终端打印当前阶段；launcher_state.json保留实际状态。断开后用同一条python start.py命令继续；已经完成且版本一致的阶段会跳过。

如果服务不可用、响应始终不符合协议、关键信息不足或超过返工上限，程序记录failed和具体错误。修正配置或补充视频后使用新的output.directory重新运行；不会为了凑齐文件而越过质量检查。

## 5. 拿到结果

完成时终端显示COMPLETE以及result.json位置。该文件列出.blend、GLB、RGB/深度/点云数据目录、质量报告、数据验证报告和DELIVERY.zip。

没有现场尺度依据时统一标estimated_m。深度来自最终模型，是正相机Z深度；它与原视频测量深度、沿射线距离不同。RGB保留水/玻璃反射，几何深度取首个表面；点云颜色是对应像素RGB。
