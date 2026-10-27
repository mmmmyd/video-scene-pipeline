# 工程结构和执行协议

## 单入口与配置

start.py → scene_pipeline.entry → launcher.preflight/run_project。project.json开头集中声明模型服务；内部attempt配置由程序自动生成，用户不需要管理阶段配置文件。

launcher在启动前检查模型角色/能力声明、地址、密钥变量、输入视频、Blender及输出参数。它不会猜服务模型ID，也不会将HTTP帧分析冒充完整视频理解。服务凭据从环境变量读取，不写入模型任务。

## 平台调用

providers.py包含四个传输：chat_completions、image_edits、json_http、command。前两者依据[官方Chat Completions接口](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)和[官方图像编辑接口](https://developers.openai.com/api/reference/resources/images/methods/edit)实现请求格式；兼容服务是否支持选定模型和参数以实际响应为准。

视觉输入将真实图片编码为data URL；JSON任务同时内联观察、约束、参考和模型报告。图像编辑将索引帧组织成联系表，通过image[]上传，解码data[].b64_json。json_http发送model/task/context/images，服务返回任务模板所规定的JSON，也可返回images_base64。

command适配器接收--request/--response或你指定的argv，不经shell拼接。请求包含kind、request_id、prompt、run_root、artifacts、response_template。适配器必须返回同一request_id，图片返回可读取绝对路径，代码放files[].content。

```json
{"request_id":"本轮请求ID","status":"ready","files":[{"path":"generated/blockout.py","content":"def build(context):\n    return {}\n"}]}
```

代码写入前做路径、语法及build(context)契约校验。生成文件限制在当前运行的generated/中；实际代码在用户选定的Blender进程执行。返回JSON不是通过验收，后续必须运行并检查。

## 自动建模和MCP

automatic.generate_model按blockout、connections、materials三阶段调用builder，将已生成代码传给后续阶段。首批必须建立观察表中的对象、所有DATA_相机、环境和光源；后续保留其对象名。三个插件都通过build(context)接收run_root、scene_name、config和reference_manifest。

blender_bridge.managed_model从vendor启动一个独立隐藏Blender进程及官方插件服务器，分配本地端口，检查实际打开文件，再通过本地execute协议调用runtime。工作结束只关闭自己创建的进程，不需要已有GUI连接。该socket层是官方插件的内部传输，不能冒充所有厂商通用MCP客户端协议。

CLI后端执行同一runtime。渲染和数据导出始终在独立后台进程完成。每个插件后保存checkpoint；失败时report保留最后成功检查点，供自动修复读取。source库存只读查询，不覆盖原始文件。

## 质量约束

分析必须提供parts、明确Blender对象名、实拍帧证据、ROI材质描述、constraints、review_targets及unknowns。每个ROI部件有自己的材质检查项和归一化source_roi，程序产出原图裁剪。

数值约束包括：height_delta（顶面世界Z包围标高差）、ratio（指定轴尺寸比）、touching_bounds（AABB间隙粗检）、opening_clear（声明起点以下的向下射线）。范围要有视频依据，允许估算；包围盒/抽样检查只证明各自范围，不保证所有真实表面连接。

model阶段生成main/top/reverse/detail四类实际检查图。quality将原始实拍区域与模型图组成明确标注“未配准”的对照，不把未标定的像素差冒充现场精度。每项审查要有实际查看声明、具体findings、pass/fail/uncertain和问题单；缺项、版本不符、uncertain或未解决问题不能定版。

代码和记录只能约束流程，不能证明模型真实看图或其语义判断一定正确。纹理相似性由配置的视觉审查模型负责；必要的人工抽查可在结果上进行，不是正常自动流程的中途必填步骤。

## 自动返工

launcher逐阶段调度。几何/语义失败时将模型、audit、review和执行错误交给repair任务，生成repair.py，从上轮模型或最后成功检查点建立下一attempt。旧通过记录不能证明新模型，下一轮重新分析、渲染、审查。普通响应格式错误先在配置限额内重试；超过上限记录failed，不自动放宽约束。

状态和产物绑定源输入、代码、观察、模型、对照图和审查SHA256。凭据环境变量可轮换；已开始运行的配置和产物不可随意改写，需要变更时使用新输出目录。

## 最终数据

冻结同一求值网格供RGB和BVH使用，涵盖修改器、曲线及实例。shader displacement/体积/粒子等未支持内容要求预烘焙，不能静默省略。光轴深度、点云和相机参数的内外参与帧一致，并有原生投影和射线抽检。

- 世界Z向上；CV相机X右/Y下/Z前，Blender相机朝本地-Z、+Y向上。
- 像素中心从左上(0,0)开始；合成相机零畸变。
- depth_z为float32正光轴Z，NaN无效，附valid_mask/K/双向位姿。
- X_cv = Z * inv(K) * [u,v,1]；X_world = T_world_from_cam_cv * [X_cv,1]。
- 每个模态采集帧写相机/世界PLY，按point_stride采样；每帧RGB和相机JSON均保存。
- approach从较远位置向指定最近端机位水平接近；固定朝向/焦距，逐帧真实渲染。
- 视频实际解码确认帧数/fps；未实现车辆扫掠或动力学认证。

生成的.blend是材质主交付。GLB是附加几何复用格式，程序纹理未烘焙时外观可能不同。
