# 外接视频衔接实验 Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task by task when that skill is available. 用户指定由 Grok 执行；不要替用户创建其他 Agent 任务。Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在独立分支与 DS 实例中，让 Director 根据用户的衔接要求，把上一镜成品视频通过 VAE 重编码传入下一镜，并提供可检查的 UI 与真实 Job 证据。

**Architecture:** 镜头保存结构化视频 context 设置，canonical submit 在执行时解析/冻结真实源视频。现有 Job runner 把视频作为独立逻辑输入上传；builtin adapter 接入 Motion Context，自定义工作流只覆盖显式映射的视频文件输入。Agent 使用结构化工具，设置和提交分别返回真实结果。

**Tech Stack:** Python/FastAPI/Pydantic、React/TypeScript/Vite、现有 Harness、ComfyUI 0.38.0、H3 Ref2AV + Motion Context、已有 ffmpeg/ffprobe；不增加模型或节点依赖。

**Spec:** `docs/superpowers/specs/2026-10-04-h3-video-context-design.md`。先读完整设计，再执行各任务。

## Global Constraints

- 只在 `codex/h3-video-context` 和 `C:/Users/AIBOX/.codex/worktrees/h3-video-context/Director-Studio` 修改与提交。
- 基线 `8bf0b85fa3a7ae4d3500330851064a122be3acdc`；主目录未提交的 latent/4-step 实现不在新分支上，不要 switch、stash、commit 或修改主工作目录。
- 端口固定：frontend 5174 / backend 8792 / Harness 8793；ComfyUI 8188 和本地 LLM复用，GPU 测试串行执行。
- 数据、profiles、Harness token/session、日志/PID 独立；不能写入主实例 data，也不能修改其激活 workflow。
- `DS_VIDEO_CONTEXT_ENABLED` 默认为 false，实验 launcher 显式开启。
- 旧 Shot 的 context 默认为 off；旧 v2 profile/hash 保持；视频 mapping 使用 v3。
- 内置默认视频 context 22 帧，合法窗口 5/22/39/56；默认只传画面。显式携带声音时音频默认 24 帧。
- 交付保持原定 F 帧；不得改变 MV 原曲权威性、歌词时间、音频 ref 选择或已有 Layout/风格圣经约束。
- 外部视频上限 200 MiB、60 秒；ffmpeg 参数数组执行；不同宽高比超过 1% 时先报错。
- 不通过关键词规则自动触发衔接；不造 Job ID，不自动替用户重做上一镜；不将末帧预览冒充视频 context。
- 自定义 workflow 的未映射参数保持上传值；没有认证的 variation/FPS 必须如实说明。
- 本轮只交付实验分支和服务，不 merge、不 push PR、不回复 GitHub。

## Review Focus

1. 上一镜重跑/排序变化：来源不会被偷偷换成其他镜头，提交 Job 的输入和 hash 冻结（Task 2）。
2. 视频混进 Picture 或 Audio refs：三条通道分离，当前歌曲仍引用当前区间（Task 3）。
3. context 裁掉时长：生成预留重叠，最终视频/音频交付长度正确，长镜头不静默缩短（Task 3）。
4. Agent 只说做了或只打开 UI：工具、持久化、图、Job ID 与展示一致，普通聊天没有副作用（Tasks 4–5）。
5. 独立服务连错主实例或共享 GPU 时抢占：代理、Harness、data/PID 身份正确，串行测试不误停其他服务（Tasks 1、6）。

## 工作区现状与本机证据

已完成：新 worktree/branch；以下基线测试已运行，38 passed：

```powershell
Set-Location C:/Users/AIBOX/.codex/worktrees/h3-video-context/Director-Studio/backend
python -m pytest tests/test_h3_workflow_inspector.py tests/test_h3_workflow_validator.py tests/test_h3_profile_runtime.py -q --basetemp=.pytest-video-context-baseline
```

当前只有设计和计划。实验服务还没有启动，也没有实现外接视频功能。frontend/harness 的 npm dependencies 由 Task 1 安装并验证。

本机 A/B 输出：`C:/Users/AIBOX/dev/Director-Studio/.run/latent-ab-20261004/`。
正式源视频 `source2.mp4` 的 Job 为 `job_bf31b0fb9347`；重编码首段 `pixelsB1.mp4` 为 `job_a3a49e1eedc9`。对应 `.executed.api.json` 是 Comfy 实际运行图。不要拿三视图预检 `source.mp4` 作为正式样本。

## 文件与职责

| 单元 | 文件 | 职责 |
|---|---|---|
| 隔离服务 | `scripts/start-video-context.ps1`、`scripts/stop-video-context.ps1`（新） | 固定独立端口/data/env/PID，复用现有启动能力 |
| API 代理/开关 | `start.ps1`、`frontend/vite.config.ts`、`backend/app/config.py`、`backend/app/api/health.py` | 可配置代理，实例身份和实验能力 |
| 设置与来源 | `backend/app/core/projects/video_context.py`（新）、`models.py`、`backend/app/api/projects.py` | 上传、上一镜解析、配置保存、source provenance |
| 图适配 | `backend/app/pipelines/h3_ref2va/video_context.py`（新）、`workflow.py`、`pipeline.py` | 独立视频输入、时间预算、Motion Context、交付裁切 |
| profiles | `backend/app/workflow_profiles/h3/{models,inspector,validator,store}.py` | 可选文件映射、v2 兼容/v3 契约 |
| transport | `backend/app/core/comfy/client.py`、`backend/app/integrations/comfy_mcp.py`、`backend/app/core/jobs/execution_adapters/{comfy,comfy_mcp}.py` | 验证两种既有执行路径均能上传视频 |
| Agent | `tool_schema.py`、`tool_execution.py`、`tool_handlers/video.py`、`DIRECTOR_SKILL.md`、`writer_context.py`、`backend/app/core/managed_runs/store.py` | 结构化工具、上下文、真实结果、fingerprint |
| UI | `frontend/src/features/director/VideoContextPanel.tsx`（新）、`ShotWorkspace.tsx`、`DirectorPage.tsx`、`api.ts`、`frontend/src/shared/api/types.ts` | 当前镜头设置和自动展开 |
| 自定义输入 UI | `frontend/src/features/settings/{H3WorkflowSetup.tsx,types.ts}` | 明确选择文件节点/字段，展示支持能力 |

注意：基线的 `fill_profile_graph` 会清除 H3 的 `ref_videos.*`。本方案通过单独 `context_video` 输入接入 Motion Context，不混入该引用槽，也不靠删除这段清理逻辑凑兼容。

## Task 1 — 独立实例可正确聊天、识别和停止

**Files:** 上表隔离服务/开关文件；新建 `backend/tests/test_video_context_launcher.py`，更新已有 `test_harness_launcher.py`；新建 `frontend/vite.config.test.ts`。

**Interfaces:** launcher 设置 `DS_VIDEO_CONTEXT_ENABLED=true`、`DS_BACKEND_URL=http://127.0.0.1:8792`；`GET /api/health` 增加可选 instance/capability 身份，不输出密钥。原实例默认行为不变。

- [ ] 在 worktree 安装前端与 Harness 依赖：各自运行 `npm ci`。使用已有可导入 uvicorn/app 的 Python，不重装 Comfy。
- [ ] 写失败测试：非默认 frontend 端口仍必须代理到指定 backend；独立 token/PID；占用端口属于其他进程时报错；停止脚本拒绝 PID 已复用且命令/工作目录身份不符的进程。
- [ ] 实现 launcher，复用 `start.ps1 -BackendPort 8792 -FrontendPort 5174 -HarnessPort 8793`。代理读取 `DS_BACKEND_URL`，未设置仍默认 8790；启动时先设 env，再让 Python import/settings 初始化。
- [ ] 设置 `DS_DATA_DIR=<worktree>/.run/video-context/data`；其余路径分别为 `DS_JOBS_DIR=<data>/jobs`、`DS_PROJECTS_DIR=<data>/projects`、`DS_LIBRARY_ROOT=<data>/library`、`DS_LIBRARY_DIR=<data>/library/actors`、`DS_WORKFLOW_PROFILES_DIR=<data>/workflow_profiles`，不能全部指向同一目录。保持 backend `.env` 本地且 ignored；只在本机配置所需 LLM/MCP 环境，禁止输出/提交 token 和 key。
- [ ] 若现有 launcher 会复用/启动 llama-swap，使用健康检查复用；实验停止不关共享 LLM/Comfy。隐藏窗口、日志与 PID 分开记录，重复启动只复用已验证为本实例的进程。
- [ ] 运行 launcher 单元测试和 Harness launcher 现有测试。实际验证 8792 health、8793 runtime 和 5174 `/api/health` 都是实验身份；8790 仍为主实例。记录结果后提交此 task。

完成后的目标启动命令：

```powershell
Set-Location C:/Users/AIBOX/.codex/worktrees/h3-video-context/Director-Studio
.\scripts\start-video-context.ps1
# 测试地址 http://127.0.0.1:5174
# 手机局域网地址 http://192.168.50.110:5174（以机器当时 IP 为准）
.\scripts\stop-video-context.ps1
```

## Task 2 — 保存配置、上传和解析真实来源

**Files:** `backend/app/core/projects/models.py`；新 `video_context.py`；`backend/app/api/projects.py`；新 `backend/tests/test_video_context_sources.py`、`test_video_context_api.py`。

**Interfaces:**

```python
class ShotVideoContext(BaseModel):
    mode: Literal['off', 'previous_shot', 'external_upload'] = 'off'
    source_shot_id: str | None = None
    source_job_id: str | None = None  # None follows source Shot's current Job
    source_output_key: str = 'video'
    upload_id: str | None = None
    context_frames: Literal[5, 22, 39, 56] | None = None
    audio_context_frames: int | None = None
    carry_audio: bool | None = None  # builtin: None means false; custom: inherit

# New internal unit owns these names; adapt imports without renaming the API.
def configure_video_context(project_id: str, shot_id: str, config: ShotVideoContext) -> dict: ...
def resolve_video_context(shot: Shot, *, width: int, height: int) -> ResolvedVideoContext | None: ...
```

`ResolvedVideoContext` 是内部 dataclass，字段：`filename: str`、`data: bytes`、`provenance: dict`。API 不暴露 data 或宿主文件路径。

API：`PUT /api/shots/{shot_id}/video-context`、`POST /api/projects/{project_id}/video-context/uploads`（multipart file）；读取沿用 GET Shot/read_status。feature flag 关闭时新增写入口返回清楚的 disabled 错误。

- [ ] 写失败测试并运行：首镜、跨项目、非相邻来源、已删除来源、源当前 Job 执行中/失败、显式成功历史版本、多候选 artifact、没有输出文件；错误不改变现有配置。
- [ ] 写外部上传测试：合法 video+audio、无音频、伪扩展名、超过 200 MiB/60 秒、无视频流、路径越界；文件名由后端 ID 生成，上传失败无可用记录。
- [ ] 根据 spec 实现配置验证和 project 上传记录。`previous_shot` 保存实际 predecessor ID；解析默认跟随当前 `h3_job_id`，不从 `list_jobs` 任取“最新成功”。多 artifact 先完成选择。
- [ ] 源字节读一次后计算 hash；provenance 记录 source shot/job/output 或 upload ID、SHA-256、实际媒体元信息。在 canonical submit 中调用它，并把来源信息写到当前 Job params，字节进入当前 Job 的输入集合。
- [ ] 测试配置后 reorder 时拒绝错位来源；提交后源 rerun/替换不会改目标输入；重启后配置能读取；旧 Shot 不受影响。通过后提交此 task。

必须保留现有 Layout/提示词/音频 preflight。视频准备失败时不能返回成功提交，更不能让 Agent 报一个不存在的 Job。

## Task 3 — 视频上传、工作流映射和正确交付时长

**Files:** 新 `backend/app/pipelines/h3_ref2va/video_context.py`；`workflow.py`、`pipeline.py`；profile 单元；transport 文件；新 `backend/tests/test_video_context_graph.py`、`test_video_context_transport.py`、`test_video_context_profile.py`；更新 `frontend/src/features/settings/` 的 mapping UI。

**Interfaces:** Job params 增加 `context_video_key='context_video'` 和 immutable `video_context_source`。runner 的 `inputs` 中该 key 是 MP4；它不能被图片 upload fallback 当作 Picture。profile v3 增加可选 `context_video: {node_id, input_name}` 文件映射。

新 graph unit：

```python
def context_generation_frames(delivered_frames: int, context_frames: int, *, max_frames: int) -> int:
    minimum = delivered_frames + context_frames
    result = minimum + ((5 - minimum) % 17)
    if result > max_frames:
        raise ValueError('video_context_length_exceeds_limit')
    return result

def attach_video_context(graph: dict, *, uploaded_video: str, delivered_frames: int,
                         context_frames: int, audio_context_frames: int,
                         carry_audio: bool) -> dict: ...
```

返回新图，不修改输入模板。Builtin 默认 22/24；custom 的未映射窗口/音频参数保持上传值，不调用 builtin graph surgery。

- [ ] 写失败测试：MP4 使用 `context_video` key，不能进入 image/audio refs；无 context 的图不新增节点；旧 v2 profile 字节/hash/状态保持；不支持的 custom profile 明确报错。
- [ ] 加入时长测试：`context_generation_frames(124,22,max_frames=362)==158`；采样 158、去头 22、去尾 12，交付 124 帧；362+22 超限抛异常。测试 audio 也按 124/24 秒裁切，MV music_segment 保持原值。
- [ ] 补 Comfy 与 Comfy MCP 的 MP4 上传测试。现有 MCP 使用 `upload_file(paths=...)`，不能因为 HTTP 试跑成功就跳过 MCP 验证。传输返回的 subfolder/filename 必须完整保存；每 Job 独立名字，无共享可覆盖上一镜输入。
- [ ] 内置适配器按实际图发现/验证角色，不绑定试跑的 901/903 这些 node IDs：LoadVideo → GetVideoComponents → Motion Context(context_frames、可选 context_audio+audio VAE)，BasicGuider 使用新增 conditioning，decode 后 Trim 重叠，再截尾至 F，SaveVideo 接裁切后输出。缺少任一必要节点类/角色时阻止提交。
- [ ] 基线 builtin 是已提交版本，并非当前主目录的 4-step 草稿。实验配置可以用已成功的 `source2.executed.api.json` 对照建立本分支的独立 fast 4-step 模板：保留 model/LoRA/scheduler/attention 参数，删除试跑 SaveLatent 与固定 Picture 绑定。检验 live 节点/模型文件后再使用；不批量复制主目录未提交代码。
- [ ] custom profile 用户选视频文件节点+输入字段；校验该字段为现有文件输入、节点可达选定输出。提供视频时只写上传 filename；没有视频时明确要求来源，不使用残留默认文件。profile v3 验证/试跑接口允许指定测试 upload ID，输出选择继续沿用已有逻辑。
- [ ] 视频同 24fps/目标尺寸时按字节复制；需要改 FPS/分辨率时使用已有 ffmpeg 能力归一化，保留宽高比并记录转换。新增测试：30fps、较大同宽高比、不同宽高比拒绝、窗口帧数不足、缺音频但用户要求带声音。
- [ ] 运行新增测试和原 `test_h3_ref2va_graph.py`、`test_h3_profile_runtime.py`、`test_h3_workflow_validator.py`、`test_comfy_mcp_client.py`。通过后提交。

图的两个声音通道必须独立：已有 `ref_audios` 是当前镜头歌曲/音色；`context_audio` 是源视频的可选连续声音。默认关掉后者不应删除前者。

## Task 4 — Agent 用工具开启衔接，真实结果驱动反馈

**Files:** `tool_schema.py`、`tool_execution.py`、`tool_handlers/video.py`、`DIRECTOR_SKILL.md`、`writer_context.py`、`backend/app/core/managed_runs/store.py`；新 `backend/tests/test_video_context_tools.py`；更新 `test_managed_h3_start_tool.py`。

**Interfaces:** 新 tool `configure_video_context` 参数与 Task 2 的 config 相同，加 `shot_id`。由工具后端推导上一镜，不让模型填宿主路径。返回 `{ok, shot_id, video_context, source_job_id, blocked_reasons, actions}`；成功 action 为 `configure_video_context:{shot_id}`。

- [ ] 写失败测试：配置工具保存真实状态但不调用 start；关闭会清理配置；来源异常返回失败；start 返回真实 Job；失败的 start 没有 fabricated ID。重复相同配置幂等。
- [ ] 把 handler 接入 legacy 和 Harness 共有的 `tool_execution`，更新 read_status；用结构化工具而不是在 chat_orchestrator/intent.py 添加“衔接”关键词判断。
- [ ] Skill/tool description 写清：用户需要连续动作/运镜时先读 source 状态并配置 previous_shot；缺来源要说明；只设置不能说已生成；明确生成请求才调用现有 start_h3_video；关闭后回普通模式。
- [ ] Writer 获得真实 source 的末帧预览和元信息；预览不替换真实视频输入、不新增虚构 Picture/Audio slot、不注入视频文件名/歌名作为创作指令。context 改变时提示词 freshness 和 managed fingerprint 反映它；off 的旧模式保留兼容。
- [ ] 拿自然语言测试 Agent：“第二镜接着上一镜往前推”“继续刚才的动作”“不要衔接，独立生成”。检查工具轨迹，不靠期待文本包含某个关键词来通过。明显不要求衔接的“这两段歌词如何衔接”不得修改 Shot。
- [ ] 测试 managed run 前一镜完成后下一镜能解析其真实视频，配置变更不会越过已有 coordinator/event/idempotency 约束。通过后提交。

## Task 5 — 当前镜头可见的视频窗口和手机版兼容

**Files:** 新 `VideoContextPanel.tsx`；`ShotWorkspace.tsx`、`DirectorPage.tsx`、`api.ts`、shared types；必要时复用 `MobileShotDrawer.tsx`；新 `VideoContextPanel.test.tsx`，更新 Workspace/DirectorPage 测试。

**Interfaces:** UI 读取同一个 `Shot.video_context`。`configure_video_context:{shot_id}` action 选中真实 target Shot 并展开设置；保存依 Task 2 PUT，上传依项目上传 API。

- [ ] 写测试：旧 Shot 显示 off；成功 tool action 选中 target 并展开；失败 action 不显示 configured；选择之前视频版本、关闭和上传均刷新后端保存状态。
- [ ] 在现有 Production 区增加紧凑面板，展示来源镜头、真实 Job/artifact、视频预览和实际窗口。builtin 显示默认 22 帧约 0.92 秒，可选合法窗口；custom 显示“工作流配置”，未映射参数只读。
- [ ] 连续性控制和“生成”按钮分开；阻塞来源的原因就近显示。视频默认 paused、`controls playsInline preload=metadata`，不自动全屏。
- [ ] 不新增 Music tab/第三列或修改全宽歌曲播放器。手机复用当前 drawer/scroll 容器，保证上下滚动、输入框和返回操作。
- [ ] 在 `frontend` 跑 `npm test -- --run` 与 `npm run build`；检查 5174 桌面和手机宽度下 UI。通过后提交。

## Task 6 — 串行实测与 Grok 交付

**Files:** 新 `docs/superpowers/reports/2026-10-04-h3-video-context-verification.md`。视频/截图/完整运行输入放 ignored `.run/video-context/`，文档只引用路径和必要诊断。

- [ ] 验证所有 durable paths 在实验 worktree；5174 的 `/api/health` 是实验身份，8793 Harness 可用；主实例 profile/hash 与项目文件未被写入。停止/重启实验实例后配置仍存在。
- [ ] 测试时主实例不发 Agent/GPU 工作；确认其 generation_count/owner 和 Comfy queue 空闲。复用同一 Comfy，不重启/卸载它。若不空闲等待用户结束主实例任务；不要直接抢占。
- [ ] 新建专用小项目，只导入需要的安全参考和 source2.mp4，不写入用户原项目。用 Agent 自己调用工具完成 source → target 的配置、提示词与真实 Job 提交；测试者不给假 Job/暗中代交。
- [ ] 固定同一 source、提示词、seed、分辨率、steps，分别跑不衔接与视频衔接；同时保留已有 raw latent 样本作参考。记录实际生成图、source hash、Job/Comfy IDs、交付帧数。
- [ ] 连续三次续接，审核接缝前后帧、人物/道具、运镜方向、色调/细节与成品时长。检测 GPU 运行失败、Agent 漏工具/误选源、窗口只改 UI、MCP 不支持 MP4 等错误，修复后重跑对应失败用例。
- [ ] 至少测试一次外部上传和一次导入 Ref2AV + Motion Context 变体的文件 mapping；并验证关闭衔接后普通生成。声音测试与 MV 原曲 ref 单独核对，不凭画面通过就声称音频无缝。
- [ ] 文档交付：实际成功/失败项、真实 Job IDs、可播放视频路径、模型/窗口/参数、已知限制、bug/问题清单、启动/停止命令、测试 URL 和 commit 列表。未跑的项标“未执行”，不写成通过。
- [ ] 每次 commit 前检查暂存内容，只提交实现/测试/文档；不提交 `.env`、token、会话、模型、用户 media/data。最后停留在本实验分支，服务已启动并说明状态，不 merge/publish。

## 给 Grok 的起始指令

> 在 `C:/Users/AIBOX/.codex/worktrees/h3-video-context/Director-Studio` 的 `codex/h3-video-context` 上执行本计划，先读配套 spec。把视频 context 完整接进 DS 的真实提交路径，让 Director 根据用户语义用工具设置上一镜视频，在 UI 展开来源与窗口。服务用 5174/8792/8793，独立数据，Comfy 8188；保留主目录的 latent 实验。按任务验证并提交，完成后启动实验服务，提供测试地址、视频结果及 bug 清单。不要修改主工作目录或帮 Agent 暗中代交生成。
