# 交接验证记录

日期：2026-10-23；当前工程版本1.0.0。机器可读摘要见summary.json。

- 38项自动测试通过：模型HTTP/命令协议、图片传输和multipart参考图接口、请求ID、代码路径/语法/契约、密钥不进入任务日志、源证据、ROI、几何与视觉检查门槛、旧审查及对照图变更拦截。
- CLI和自动启动的MCP工作进程均完成实际Blender链路；最终整理后的工程又使用随附vendor插件跑通完整MCP执行与断点继续。
- 实际Blender版本5.2.2 LTS。测试从一个本地MP4输入开始，自动分析、参考图任务、参考核对、三批代码生成、建模、四视角、质量检查、自动修复、重检、数据导出和打包。
- 第一次视觉审查由测试服务故意返回失败；程序自行生成repair.py、建立下一轮、重新审查并完成输出，全程没有手工填写中间文件。
- 最终产物：可编辑.blend、GLB、8张RGB、6份float32 Z深度、12份相机/世界PLY、两段各2秒视频及相机参数。
- 数据校验通过，最大反投影重投影误差约7.95e-14像素；这只是模型数据一致性，不是实景几何或材质精度。
- 相同配置再次运行可识别并跳过全部已完成阶段。

example_delivery.zip是本次软件测试的实际Blender输出样例。它使用确定性测试服务返回分析/审查和代码，不是真实大模型重建，也不代表实拍纹理相似性已经验收。

真实服务需要接手者在project.json填写实际模型/地址并设置密钥变量。本次未使用任何真实模型账号完成付费API调用；Chat Completions、图像编辑与自定义HTTP适配的请求格式和响应处理通过模拟HTTP测试。配置齐全时，真实运行走相同调度和质量门槛。

复现：

```powershell
python -m unittest discover -s tests -v
python scripts/verify_full_pipeline.py --output outputs/verify --backend managed_mcp --addon-dir vendor
```
