阶段：按问题单返工，禁止自行宣告质量通过。

读取本轮audit、逐项review、源证据与失败对照图。按blocker→major→minor处理。为每个issue写明根因、修改part_ids、具体动作、保留部件和重检target_ids。连接错误先修几何；材质错误先判断UV/光照/贴图/法线来源。不要在同一批同时改镜头、曝光、全部几何和材质。

按request.response_template输出修复代码，代码放在files[].content，并返回base_model_sha256。执行器会自动建立下一轮工作副本，不要求操作者中途执行命令。下一轮必须重渲染与重新审查，旧pass记录不能沿用来证明修改后的模型。

达到max_revisions仍失败时记录需要补充的具体视频/部件/视角，不无限重试，不把失败改为通过。
