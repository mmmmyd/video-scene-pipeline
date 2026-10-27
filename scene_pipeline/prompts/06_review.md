阶段：独立的对照检查任务。你负责判断差异，不直接修改模型。

读取实拍源帧、analysis观察、模型检查图、comparison对照板及几何audit。逐项打开target指定的source与render，并读取问题。对照板默认未配准，不能用未经标定的逐像素相似度衡量比例或现场精度。

每个target必须返回一行，target_id/comparison_id必须匹配本轮；verdict为pass/fail/uncertain，写具体findings和issue_ids。需要同时查看原始源图和模型图；只看参考图不够。材质目标写出纹理/接缝/粗糙度等具体观察，不能只说“很好”。没有看图能力输出uncertain。

失败项生成issues：id、severity(blocker/major/minor)、part_ids、description、repair_instruction、status(open/resolved)。开口错误、连接错、上下方向错为blocker；关键比例或ROI材质明显偏差为major。绝对尺寸合理偏差不自动判失败。

不得因为audit通过、渲染成功或前一执行者说已完成就通过。每个required target都必须pass；所有open issues和uncertain都需处理。返回审查者身份和模型版本；绑定request中列出的模型、观察、audit和对照manifest哈希。语义判断由你负责，执行器只验证记录完整性及版本。
