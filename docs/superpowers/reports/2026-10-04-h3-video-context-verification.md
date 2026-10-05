# H3 外接视频衔接验收

日期：2026-10-04（本机 Pacific）；分支：`codex/h3-video-context`。

## 交付范围

实验服务：前端 `http://192.168.50.110:5174`，后端 8792，Harness 8793；共享 ComfyUI 8188 和 llama-swap 11435。独立数据位于本 worktree 的 `.run/video-context/data`，未合并、推送或发布。

上传/上一镜视频经过 VAE 重编码，通过 Motion Context 进入下一镜。视频是独立输入，不占 Picture 或 Audio 槽。默认窗口 22 帧（0.92 秒），默认不继承源视频声音。导入的 Ref2AV + Motion Context 变体继承自己的窗口和音频开关。

这轮只认证官方节点形状的 Ref2AV + Motion Context、24 fps，以及同形状的导入变体；不承诺所有自定义节点、VideoExtend 或任意 FPS。长镜头超出采样上限会报错，不能静默缩短。

## 实跑证据

### 真实 Director Agent

项目 `prj_9b7c440eae2d`，安全参考：完整着装的男性吉他手、夕阳沙漠、吉他和麦克风。模型 `qwen38-27b-mtp`，通过实际 Harness 和后端工具运行。测试者只用简短自然语言请求；下表 Job 均由 Agent 调用 `start_h3_video` 提交。

| 用途 | Job | Comfy prompt | 实际输入 |
| --- | --- | --- | --- |
| 第一镜来源 | `job_3e0b968db9c1` | `8cdd04d7-f5d8-448d-b6cb-80bde20e5492` | 普通参考图 |
| 第二镜普通对照 | `job_9d9d6eea01bd` | `be94b021-331e-443b-8ecb-3614043bc5e1` | 无视频 context |
| 续接 1 | `job_d7e3d41ec4ba` | `74ca6c5e-6be5-4b66-be50-e90a5177508d` | 第一镜实际输出 |
| 续接 2 | `job_b2f541cc0135` | `d99c6506-782f-438a-9602-50351a83cc69` | 续接 1 实际输出 |
| 续接 3 | `job_dc768b6ebc65` | `f6f48b8d-ccaa-489d-a2ab-67bfa8d25b1f` | 续接 2 实际输出 |

每镜请求和交付均为 56 帧、864×480、24 fps、2.3333 秒。续接实际图采样 90 帧，去掉 22 帧重叠和 12 帧尾部补齐。三次源 SHA-256 均与上一镜输出及当前 Job 的冻结 `context_video` 文件一致。

轨迹：`revise_shot` → `write_prompt` → `configure_video_context` → `start_h3_video`。设置后 canonical submit 会重新检查提示词上下文版本。只配置不生成的工具没有创建 Job。

Agent 的普通对照和续接使用不同提示词/seed，因此只用于观察真实操作，不作为受控优劣结论。独立受控对照见下一节。

自然语言负例“这两段歌词如何衔接？只是讨论概念，暂时不要修改镜头，也不要生成”只读 status、回答讨论，未修改镜头或创建 Job。“第四镜取消衔接，改回独立镜头，暂时不要生成”真实保存 off，未提交视频；它额外修订了镜头并尝试重写提示词，遭旧麦克风备注冲突阻止，这属于 Agent 的多余准备动作，不能称作完美遵循。

### 同条件导入工作流 A/B

这是明确分开的工作流技术测试，通过 profile test API 发起，**不是 Agent Job**。

| 项目 | 普通版 | 视频续接版 |
| --- | --- | --- |
| Job | `job_22b5f9899c38` | `job_ab1588558937` |
| Comfy prompt | `85175e7a-cc71-43a1-98cc-aabe73fc9ae7` | `e4bdb122-4c75-4b9f-8ad2-8bad83038fa8` |
| seed | 42 | 42 |
| 提示词 SHA-256 | `920b5d8e9a4e3b7e3f35212aa644bf92a54fe6abdaab567661e7ddf8ece4dceb` | 相同 |
| Picture | 相同导入参考图 | 相同 |
| 采样步数、模型、LoRA | fast4 模板，4 steps | 相同 |
| context | 关闭 | 外部 source2.mp4；C22，音频窗口 24，继承声音开 |
| 实际采样/交付 | 56 / 56 帧 | 90 / 56 帧 |

外部上传 `vup_d016db58f977`，源 SHA-256：`d7b61dda7926f8ab41c29c819228b3550584fbe0ff83ad35494e5fff3e9105c8`。两个导入 profile 都通过真实 Comfy MCP 验证与执行，视频文件映射实际生效。

续接实际 Motion Context 同时接入 `context_frames`、`context_audio`、音频 VAE；普通版不存在 Motion Context。输出音频非静音、没有满幅削波，AAC 时长分别为 2.325 和 2.334 秒；差异属于编码边界，不能据此宣称听感无缝。未进行人工听感验收。

fast4 使用 Singularity Ref2AV int8 模型、Turbo 4-step LoRA、int8 视频 VAE、fp32 音频 VAE、Qwen3VL nvfp4/awq、Euler、beta 调度、shift 12/3。primary 的默认 Turbo8 模板没有因此切换。

已有 raw latent / pixels 重编码三连样本保留在主目录 `.run/latent-ab-20261004/`，仅作历史参考。这些旧样本交付帧预算不同；本轮不据此声称视频优于 latent。

### MV 原曲和音频通道

专用项目 `prj_c862503bf87d`；测试原曲从安全 source2 的音轨提取，区间 0–2.333 秒，避免使用用户原 MV 项目。

真实 Agent 已保存 `music_segment.use_as_audio_reference=true`、区间 0–2.333，并配置外部视频 `vup_723b74a33281`、`carry_audio=false`。首轮后台 Job `job_b7295a6d11b0` 被开发热重载取消。暂停热重载后的重试 `job_b70a953c413e` 成功，Comfy prompt `3fdedbeb-e7e1-4cfa-af49-41331f292ea9`。

实际执行图：`ref_audios.ref_audio_0` 接 `LoadAudio` 的 `music_audio_1.wav`；Motion Context 接视频画面，`audio_context_length=0` 且没有 `context_audio`。暂存 Audio 1 为 32000 Hz、双声道、74656 样本，PCM 与原曲前 2.333 秒逐样本相同。输出 56 帧、2.3333 秒，音频 2.334 秒。视频与歌曲两个通道都实际生效。

Agent writer 将“演奏”补成了“演唱”，并在提示词中断言 Audio 1 含人声；没有听感/歌词证据支持这个判断。这是仍需改进的模型创作判断，通道正确不能作为提示词完全正确的证据。

关闭衔接后 Agent 通过 `patch_shot_refs` 修正麦克风备注并生成 `job_5634eab69ac5` 成功，Comfy prompt `738f88d5-744b-48ec-83df-e6fb0ad653fc`。实际执行图 Motion Context 节点数为零，普通模式恢复。

## 画面与提示词审核

逐镜审核首/中/尾帧，以及三处接缝前两帧和下一镜首帧。人物、衣服、吉他、麦克风保留；镜头连续推近，无新增不安全内容。三处接缝的主体位置、姿态和背景接近上一镜末尾，明显避免普通对照重新从参考图全身构图开始。

剩余画面问题：后两镜天空逐渐偏冷，第四镜发丝边缘的暖色增强，近景有面部细节漂移。几何连续改善不等于身份、色彩和声音完全无缝。静态帧审核不替代长视频/复杂动作/快速运镜的全面质量认证。

提示词使用实际 `<Picture 1>`，没有把源视频伪装成 Picture/Audio 参考。Agent 曾自己加了“丢弃麦克风”的备注，后来明确用户要求保留麦克风，与旧备注矛盾；提示词按新 brief 保留，但旧备注仍可能触发审查。这是 Agent 创作和引用说明维护的问题，已修引用说明工具的导入素材支持。

## 发现并修复的问题

| 问题 | 判断和修复 | 验证 |
| --- | --- | --- |
| Agent 看到导入 layout，却不能保存其引用 | inventory 和 schema 不一致；导入参考用 other，保存/append/修改引用共享可见边界 | 回归 + 真实 Agent 保存 |
| 显式只绑定参考图却被强制自动配角色/场景 | 完整分镜 save 忽略显式绑定；只对空绑定保留既有自动配资产 | 回归及既有安全用例 |
| “生成第一镜”被单镜生成授权拦住 | 原 ordinal 正则覆盖不足；改为模型判断明确单镜授权及目标 ID，后端失败关闭 | 回归 + 中文真实请求成功 |
| 实验选择 fast4，冻结 Job 仍使用默认模板 | resolve 与 snapshot 使用了不同文件；统一设置来源 | 失败复现 + 成功实跑 |
| 跟随上一镜重跑/替换文件，提示词仍被当作新鲜 | signature 增加实际来源 Job、输出、内容 hash 和 runtime；等待模型期间也检查 | 来源/字节变化回归 |
| 自定义 C39/音频继承被 builtin 默认污染 | 从上传图读取实际窗口/声音；冲突覆盖或未认证形状明确拒绝 | C39/音频 True 回归 |
| 自定义续接交付短了重叠帧数 | 调整采样预算、去重叠后裁到请求 F；不改上传窗口/音频/FPS | 124 帧回归 + 56 帧实跑 |
| 导入变体验证没有提供映射的视频字段 | 结构验证补契约测试文件字段；真实试跑仍要求有效上传 | RED→GREEN + MCP 实验 |
| 不支持的工作流仍被工具说成“已配置” | 保存前验证 runtime；失败保持旧配置、不返回成功 action | RED→GREEN |
| profile test 把继承声音记成 false | 记录实际导入窗口和声音配置 | RED→GREEN + 实际执行图 |
| 手机 Production 没有衔接入口 | 实际手机/桌面 Production 增加同一个面板 | UI 回归 + 浏览器目视 |
| 跟随上一镜的预览空白 | 预览解析遗漏当前 source Job | null/pinned 回归 + 真机宽度浏览器 |
| 分镜语义校验要求把视频塞进 Audio、拒绝延后配置运行时输入 | 提供独立输入通道、项目模式及歌曲元信息；music_segment 可随 draft 保存或之后配置，video_context 在保存后配置 | 契约回归 + Agent 第二轮保存成功 |
| 开发热重载取消运行任务 | 实验启动传 NoReload；普通 start 默认热重载保留 | 启动参数回归 + 实际进程无 --reload |

失败样本还包括两次旧 snapshot 不一致 Job（`job_fe232b41c6ee`、`job_91edcf7a8ce6`）和一次开发重载取消的 profile baseline（`job_ba9273630026`）。都没有偷偷换 source、伪造 Job 或当作成功；修复后实际重跑。

## UI、隔离与重启

浏览器验证桌面 1440×1000 和手机 390×844。Production 展示真实来源 Job、56 帧视频、C22 和暂停的内联预览。手机运行页可以上下滚动到提示词末尾；Director 对话记录独立滚动，输入框位于可视区域底部。未在实体手机上验证触摸和软键盘。

实验服务停止/重启后，第二、第三镜的 previous_shot、第四镜 off 和 MV external_upload 配置仍存在；5174 health 返回 instance=video-context；8792/8793 使用独立目录/token/PID。只停止实验的三个进程，共享 Comfy/LLM 保持运行。

最终审查修复后再次重启：两份导入 profile 经真实 Comfy MCP 验证均为 valid、无错误/警告；对已配置外部视频的 MV Shot 请求 MiniMax 返回 400 `Video continuation requires the local H3 provider`，未创建 Job。浏览器实际开启、关闭外部视频 Carry source audio，磁盘配置分别为 true/false，歌曲 `use_as_audio_reference` 保持 true。2243 文件主实例稳定检查点在这次重启后仍全部 hash 一致。收尾保持服务运行，后端没有热重载参数。

主实例 54 个 profile 文件与初始 snapshot hash 一致。主项目全量初始 hash 比较不能作为“全部没变化”的证据：主服务期间有聊天活动，99 个文件已不在原路径、4 个文件改变。实验请求/写入都指向 8792 和独立目录；不回滚主实例的独立活动。收尾另设 2243 文件稳定检查点，重启/后续验收后全部 hash 不变。

生成前检查 primary generation_count=0、owner=None，以及 Comfy 队列；GPU 工作串行。空闲时 RTX4090 约 933 MiB、利用率 0%，其中 Comfy Python 约 585 MiB DedicatedUsage，其余为桌面合成/应用；WDDM 的 nvidia-smi per-process memory 显示 N/A，所以通过 Windows GPUProcessMemory 补核对。

## 验证与本地结果

最终独立审查由新上下文 reviewer 完成，结论为 With fixes：无 Critical，四项 Important、三项 Minor。四项 Important 均先复现失败，再修复通过；后端相关 99 项通过，前端面板 12 项通过。修复后完整后端 `python -m pytest -q`：1952 passed、13 skipped、两项既有 Pillow 弃用警告，443.20 秒；前端 `npm test -- --run`：293 passed（37 文件），`npm run build` 通过。

### 独立审查及修复

| 审查发现 | 处理 | RED→GREEN 回归 |
| --- | --- | --- |
| tail-layout writer 等待模型时来源改变，可能把旧 prompt 盖上新来源签名 | 写作前捕获来源签名，发布前重新检查，只保存最初签名 | `test_tail_writer_rejects_source_changes_during_review`：来源 Job 重跑、文件字节改变两例 |
| 导入图可以绕开 Motion Context conditioning，却仍被验证通过 | 认证 LoadVideo → components → Motion Context → guider → sampler → 双 decode → trim 的真实链路；交付裁切继续检查 | `test_imported_motion_context_rejects_bypassed_sampling_chain`：conditioning/video decode/audio decode 三例 |
| 配了视频衔接后切到 MiniMax，付费请求静默丢掉视频 | 在提示词写作和建 Job 之前拒绝不支持的视频 provider | `test_minimax_rejects_active_video_context_before_a_job` |
| 外部视频的 Carry source audio 开关只改组件状态 | external_upload 和 previous_shot 都保存真实配置 | 面板的开启、关闭持久化两例 |

没有二次 reviewer；按执行技能的单次审查约定，用失败复现、对应绿色回归和最终全量测试验证修复。

### 延后的轻微问题

1. 外部视频上传预览依赖组件临时状态，刷新页面或 Agent 配置后可能不显示；来源保存和生成输入不受影响。
2. 非实验实例仍显示衔接控件，操作会返回 403；应按 health capability 隐藏入口。
3. MOV/WebM 归一化为 MP4 后仍沿用原扩展名/MIME；应让传输元信息匹配真实字节。

### 执行判断及代价

1. 导入 Motion Context 也调整采样预算和交付裁切，确保返回请求的 F 帧；上传的窗口、声音和 FPS 参数仍继承。若判断错，某些有效自定义后处理链可能被拒绝。
2. 跨进程 GPU 准入保持实验范围外，采用手动串行验证。若同时从两个实例生成，仍可能竞争 GPU。
3. 任意节点类/FPS 保持认证范围外，未认证图明确拒绝。代价是其他有效工作流尚不能接入。
4. 人工听感、实体手机及复杂动作质量留作后续验收，当前只报告测量和代表帧/浏览器检查。代价是触摸、听感和长视频问题可能尚未被发现。

本地证据位于本 worktree（均 ignored，不提交会话/媒体/data）：

- `.run/video-context/agent-chain.mp4`：来源 + 三次续接，224 帧、约 9.333 秒。
- `.run/video-context/chain-seams.jpg`、`live-contact.jpg`、`profile-comparison.jpg`。
- `.run/video-context/review.html`：串联与受控 A/B 播放入口。
- `.run/video-context/live-metrics.json`、`profile-metrics.json` 和 `*.executed.api.json`：真实执行/源 hash/帧数/声音证据。
- `.run/video-context/mobile-continuation.png`、`mobile-director.png`、`desktop-continuation.png`。
- `.run/video-context/chat-*.response.json`、`mv-chat-*.response.json`：真实工具轨迹，未提交。

启动：`./scripts/start-video-context.ps1`；停止：`./scripts/stop-video-context.ps1`。修改后端后需重启实验实例，避免开发热重载中断 GPU Job。

提交前检查暂存文件与 staged-secret scanner，均通过，`git diff --cached --check` 无问题。测试参考图与逐镜代表帧审核为安全内容；没有提交媒体，没有宣称自动 NSFW 检测器覆盖所有素材。

既有实现 commits：`2674079`、`86bd43d`、`2943607`、`760351f`、`93eca34`；接手修复与最终验收在本报告所在提交中。实验分支保留供本地测试。
