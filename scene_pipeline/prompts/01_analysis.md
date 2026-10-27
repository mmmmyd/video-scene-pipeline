阶段：实拍证据、部件拆解与约束设计。此阶段不要直接创建最终模型。

1. 完整观看所有视频，再读取帧索引及联系表。记录是否完整观看；不能完整观看时说明覆盖范围并保留未知项。
已有模型任务先读analysis/source_inventory.json，确认真实scene、对象、材质和坐标；待重建新部件明确标注，不能凭记忆猜对象名。该清单是只读入口，不是最终几何验收。
2. 列出负向障碍物相关parts：稳定id、Blender对象名、角色、是否重点区域、可见程度及source_evidence_ids。材质需描述颜色、纹理大小和方向、颗粒、接缝、磨损、粗糙度/湿润/反射，并列独立纹理证据。
3. 从多个方向解释开口去向、部件接触、上下关系、相对比例，写constraints。数值是模型参数约束，并不代表现场测量。
4. 支持height_delta、ratio、touching_bounds、opening_clear。touching_bounds只是包围盒粗检；细部接触还必须在review_targets中安排视觉核对。opening_clear使用开口内代表性竖向射线，检查是否误被地坪封住。
5. 给每个检查项安排review_targets：唯一id、focus、parts、source_evidence_id、camera_role、description。必须覆盖connections、relative_proportions、negative_obstacle_materials、multiview；每个ROI部件至少有一个针对自身的材质检查项。
6. 填unknowns。关键开口/上下关系无法解释的项标blocking=true，先补证据，禁止靠默认结构补齐。绝对尺寸未知可设blocking=false。

输出到request.response_path，严格按response_template。所有证据ID必须来自inputs/manifest；不得编造帧号。允许估算比例，容差必须给出依据。不要只提交“看起来合理”。
