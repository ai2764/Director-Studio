# 外接视频衔接实验设计

## 用户目标与执行方式

用户希望在一个新的分支和独立 DS 服务中测试视频衔接。用户说需要衔接时，Director Agent 应把上一镜的真实成品视频传给下一镜，开启工作流的 context 窗口。由 Grok 实现本方案。

本次只准备分支、设计和执行计划。用户的“打开窗口”暂解释为：开启实际 context，并展开当前镜头的衔接设置面板。设置衔接本身不提交生成；“衔接并生成”等明确生成请求继续使用已有提交工具和执行约束。

## 分支与实例

- 分支：`codex/h3-video-context`。
- 工作目录：`C:/Users/AIBOX/.codex/worktrees/h3-video-context/Director-Studio`。
- 基线：`8bf0b85fa3a7ae4d3500330851064a122be3acdc`，已提交的 MV 改动。
- 主目录的 `codex/mv-h3-fast-4step` 上有未提交的 4 步和原始 latent 改动；它们没有复制进本分支。Grok 可只读参考，不能假设这些实现已经在实验分支上。
- 实验前端 `5174`、后端 `8792`、Harness `8793`，复用 ComfyUI `8188` 和现有本地 LLM。
- 数据、项目、Job、workflow profiles、Harness token/session、日志和 PID 均属于实验实例。不能把主实例的数据目录挂成可写共享目录。
- 新实例复用同一 GPU；当前 VRAM 调度器按进程管理，不能宣称两个 DS 实例已实现跨进程 GPU 互斥。本轮采用串行人工测试：主实例不执行 Agent/GPU 任务时才启动实验生成，执行前检查主实例和 Comfy 队列，实验停止脚本只停止自己的进程。

## 已验证的可行路径

本机 ComfyUI `0.38.0` 已有 `LoadVideo`、`GetVideoComponents`、`MiniMaxH3MotionContext` 和 `MiniMaxH3MotionContextTrim`。

```text
上一镜成品 MP4
  → 按当前 Job 上传到目标 Comfy 的 input
  → LoadVideo
  → GetVideoComponents
  → Motion Context.context_frames
  → H3 conditioning / sampling
  → decode
  → Trim：删除 context 重叠帧
  → 保持原定交付帧数
  → SaveVideo
```

可选声音通过 `context_audio` 和 H3 audio VAE 接入。这里的重编码是视频/音频 VAE 编码，而不只是更换容器格式。

已有独立试跑共用一段起始视频，两条路径各续接 3 次：4 步、seed 42、864×480、24 fps、22 帧视频 context、24 帧音频 context。六个续接 Job 全部成功。MP4 重编码保持了人物、场景和推镜，累计后略暗，每段约 19 秒，原始 latent 约 16 秒。这只是短片、单 seed 的可行性证据；音频波形两条都有接缝幅度变化。

本机试跑证据：`C:/Users/AIBOX/dev/Director-Studio/.run/latent-ab-20261004/`。正式源视频是 `source2.mp4`，实际执行图是 `source2.executed.api.json`、`latentA1.executed.api.json`、`pixelsB1.executed.api.json`。`source` 和 `latent1` 是三视图参考的预检，不能作为正式对照。

## 方案选择

采用现有 Motion Context 的内置视频重编码路径，先完成“成品视频输入”的完整应用链路。它无需新装节点，且保留现有官方 Ref2AV 输入边界。

独立 `MiniMaxH3EncodeAVPatched` 也是一种方案，但当前未安装；其输出的 LATENT 配对与消费节点仍需验证。原始 latent 保持在原实验分支，本分支不要求保存或加载 `.safetensors` context。

## 数据与行为契约

### 镜头衔接设置

给 `Shot` 增加可选 `video_context`，旧项目默认关闭。支持：

1. `off`：普通生成。
2. `previous_shot`：上一个 storyboard 镜头的真实成品视频。
3. `external_upload`：当前项目内上传的成品视频。

上一镜由 `Project.shot_ids` 的顺序确定，不能按创建时间、镜头标题或 Agent 猜测寻找。配置时保存明确的 source shot ID，提交时必须再次验证它仍是直接上一镜。

未显式选历史版本时，使用上一镜当前 `h3_job_id` 对应的成功视频；该指针指向失败或执行中的 Job 时阻止提交，不能偷偷退回旧成功版本。用户可以显式选择一个成功历史 Job。多候选输出未确认时要求选择实际视频 artifact，不能任取第一个。

后端在提交时读取源视频一次，计算 SHA-256，形成当前 Job 的独立输入和不可变 provenance。每个目标 Job 保存来源 shot、job、output key/upload ID、hash、实际尺寸/FPS、窗口、转换参数。上一镜随后重跑或替换文件不改变已经提交的目标 Job。

外部视频只能通过项目内的上传记录定位；不接受 Agent 提供任意系统路径或 URL。上传先完成文件大小、ffprobe 视频流/时长检查；失败不生成有效上传记录。初期允许 MP4/MOV/WebM，上传上限 200 MiB、源时长上限 60 秒；媒体处理采用参数数组执行 ffmpeg，不使用 shell 拼接。

### Context 和交付时长

内置实验适配器默认视频 context 22 帧，支持 5/22/39/56；开启后必须真正进入 conditioning。图中没有 `context_latent` 输入，不需要原始 latent 文件。

默认只续接画面，避免把上一镜生成的声音覆盖当前歌曲输入。内置适配器允许显式 `carry_audio=true`，默认音频窗口 24 帧。自定义工作流的声音/窗口由工作流拥有；未映射参数不能假称 DS 已覆盖它们。

设现有逻辑要求的交付帧数为 F，context 为 C，采样长度为 `L = 最小的满足 L >= F+C 且 L % 17 == 5 的整数`。先生成 L，再裁掉前 C 帧和多余尾帧，最终交付 F 帧。音频裁切同样按 24 fps 的时长执行。不得因加 context 缩短下一镜的歌词区间或更改 MV 原曲裁切逻辑。超过当前模型/DS 长度限制时返回明确错误，不能静默缩短。

默认 builtin FPS 为 24。成品视频在必要时归一化到 24 fps、目标分辨率；同 FPS/尺寸的视频直接复制。初期不同宽高比超过 1% 时拒绝并说明，避免隐式拉伸人物。自定义工作流保持其 FPS 参数；本轮只认证 24 fps 的 context 实验图，其他 FPS 的衔接不宣称已支持。

### 自定义工作流输入边界

增加可选 `context_video` 文件输入映射：明确 `node_id` 和 `input_name`，DS 只写上传后的 Comfy filename。校验映射节点、文件字段和到选定输出的路径。模型、LoRA、steps、scheduler、sampler、VAE、窗口和解码由工作流拥有。

旧 v2 profiles 继续原样读取，其 mapping hash/激活状态不变化。使用新视频映射的 profiles 使用 v3 契约。自定义图一旦声明必需 video input，没有视频时必须阻止运行，不能继续用导入时的旧文件名。

首轮覆盖 builtin Ref2AV + Motion Context，以及导入的同形变体。issue #22 的 `MiniMaxH3VideoExtendPatched` 有不同的 Ref2AV/引用接口，全面接入不属于本轮；不得只加入一个类名就宣称兼容全部 variation。

### Director Agent

新增结构化工具 `configure_video_context`。Agent 根据完整对话理解“接着上一镜动作”“连续推镜”等语义并调用工具；后端只验证结构化参数和真实状态，不新增自然语言关键词/正则触发器。

工具返回保存的配置、可解析的源视频 Job、实际窗口、阻塞原因和当前 Shot。Agent 可以报告“已设置衔接”，只有 `start_h3_video` 返回真实 Job ID 后才能说“已提交”。关闭衔接同样使用工具。缺少上一镜、来源不明确或失败时如实说明，不创造来源 ID、不代替用户补生成上一镜。

`read_status`、prompt writer 和 managed run fingerprint 都包含衔接信息。Writer 获得来源末帧作为视觉证据及 context 元信息；末帧仅用于观察，实际生成必须传成品视频，不能退化成尾帧 I2V，也不能把视频声音偷偷占用 `<Audio N>` 歌曲槽。

### UI

在现有 Shot Workspace 的 Production 区放紧凑的“视频衔接”设置；不加顶级 tab。显示来源镜头/视频版本、真实 Job ID、预览、窗口和关闭按钮。Agent 成功配置后选择目标 Shot 并展开此区域；不能只弹开 UI 而不保存配置。

手机端保持对话输入框可见、页面可上下滚动。播放器不自动播放、不放大成全屏，不修改已有音乐播放器布局。

## 成功条件

- 独立实例能正常聊天和生成，5174 的 API 请求只进入 8792，Harness 为 8793。
- 自然语言要求衔接后，保存真实配置，UI 展示，生成图含正确的视频输入和 Motion Context。
- 图片/音频引用顺序、风格圣经、MV 歌词/原曲区间及正常无 context 的模式不受影响。
- 成品长度保持原定 F 帧，重启后配置及 Job provenance 可追踪。
- 完成至少一对不衔接/衔接的同条件视频，以及连续三次续接；人工检查接缝、动作/运镜、人物和色调，问题与真实 Job ID 一起记录。
- Grok 交付 commit、启动命令、5174 测试 URL、验证结果和问题清单；不合并、不发布。
