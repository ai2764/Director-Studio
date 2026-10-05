# 视频衔接 Writer 修复

修改前检查点：`0bc2fab`；实验分支 `codex/h3-video-context`。

## 原因和处理

原来已有来源 Job、窗口、声音开关等元信息，但普通 Writer 接到的末帧图片没有明确的图片身份说明。视频衔接也走普通六段写作路径，仅有引用/格式检查，没有独立视觉接缝审查。本地模型实测会照抄旧的侧脸近景开场。

- 明确标注 Writer Image 1 为来源视频末尾的观察图，不增加 H3 Picture/Audio 槽，不使用虚构的 `<Video N>`。
- 要求从实际末尾姿态、构图和位置出发，描述到新镜头目标构图的动作与运镜；区分静态可见事实和无法从单帧确认的运动。
- 激活视频衔接的镜头复用已有尾帧候选写作及独立接缝审查；无需创建尾帧 Layout。写作和审查都收到实际来源末帧。
- 保留输出 token 上限、schema、超时及一次修复预算；视觉审查拒绝后不发布候选，不提交生成 Job。既有作者意图/对白/参考保护继续执行。
- 关闭源视频声音只影响该通道，不删除当前 Audio refs、原曲区间或画外对白。
- 旧的活跃衔接提示词签名失效，下次写作/提交会走新流程；off 镜头签名保持原值。

## 验证

新回归先复现六项失败，再修复通过；另复现了无尾帧 Layout 的视频镜头错误走普通 Writer 的路由问题。测试覆盖真实请求图片角色、无新增参考槽、文本回退明确未见末帧、off 不附旧图片、bounded 图片请求的 schema/输出预算、旧签名失效及拒绝候选不得发布。

最终 Writer/参考/对白/上下文/重试/client 回归 171 passed（98.14 秒）；最终视频输入与 managed/Harness 回归 127 passed（8.73 秒）。两组有重叠，不相加。测试包含新路由和拒绝候选用例。

学校项目 `prj_84e67b60b882` 的实际第一镜末帧显示背后中景，小美面向教学楼行走。裸写作技术 probe 仍复制旧侧脸开场，故没有作为成功交付。随后在 ignored 隔离副本中调用完整 `DirectorService.write_prompts_after_layout`，使用真实本地 `qwen38-27b-mtp` 和实际末帧；热身模型的生命周期适配器为 probe 简化，写作、对白编译、参考保护、视觉审查和保存使用真实服务代码。

最终开场为 “The camera follows 小美 from behind as she walks through a crowd ...”，之后才描述画外呼喊、减速和回头。实际独立视觉审查给出 valid=true，确认来源与候选开场都是背后中景。六段格式、Picture 1/2 和原对白通过服务校验。原学校项目 JSON hash 前后不变；没有代 Agent 修改项目或生成视频。这只验证写作与审查，不保证视频成片无缝。

本地证据（ignored）：`.run/video-context/writer-fix-red.log`、`writer-routing-red.log`、`writer-fix-final-regressions.log`、`writer-school-tail.png`、`writer-school-service-probe.result.json`。

本修复不包含聊天意图与开关状态的自动一致性检查；开关仍由 `configure_video_context`/UI 保存的真实配置决定。

## 运行加载

确认实验生成队列与 Comfy 队列空闲后，仅重启实验的 8792/8793/5174 服务，保留共享 llama-swap 与 Comfy。5174 局域网代理健康检查确认 instance=video-context、video_context=true、Comfy 与 LLM 可达；Harness 独立健康检查 sidecar_ready=true。
