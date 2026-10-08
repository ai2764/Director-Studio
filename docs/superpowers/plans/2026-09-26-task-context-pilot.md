# DS P0/P1A Task Context Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. User selected native inline execution. The steps below retain the original implementation recipe; verification evidence is kept locally.

**Goal:** 为 Director 的单 Shot 提示词任务提供带来源、版本和按需查询能力的上下文工作包，并测量真实输入与信息完整性，保持已有权限、生成和恢复边界。

**Architecture:** 同一个 Agent 使用 overview / shot_prompt 两类工作包，底层事实仍来自现有项目与 Job 存储。外层 Agent 负责补查和切换视野，普通及尾帧 writer 使用共享证据；不创建新的 Agent 循环。先 shadow 只读比较，再在显式选中的测试项目启用 pilot。

**Tech Stack:** 现有 Python 3.12、Pydantic、pytest/pytest-asyncio、FastAPI、Legacy/Harness；无新增依赖。

**Spec:** `docs/superpowers/specs/2026-09-26-agentic-state-refactor-design.md`，提交 `449ad74`。

**Status:** 用户批准 Native 执行；六个步骤已实现并分步提交（实际执行基线 `06190fc`，实现提交 `1faf1ad` 至 `c6ab049`）。验证和独立审查记录保存在本地。真实模型对照未执行，默认 off、空 allowlist。这里只实现 P0/P1A，P1B—P4 不属于本清单。

## Global Constraints

- 用户原则：降低框架错误率，保留 LLM 创作自由；避免角色、服装、场景、措辞等业务关键词硬编码；修系统而非手动修某部影片；用传统软件工程可理解的方式解释取舍。
- 任务划分不等于多个独立 Agent，也不等于固定工作流。
- 切换仅改变视野，不产生业务写入，也不重置工具、重试或资源预算。
- 摘要是导航，不是新的事实权威；原始聊天和业务记录不被删除。
- P1A/P1B 保留现有 JSON 存储、单后端进程与已有恢复机制，只做上下文和业务边界收敛。
- 真实模型验证是独立、显式授权的测试，不因为本计划存在就重拍用户影片。
- 第一阶段不新增数据库、外部服务、成片 QC、自动重拍、模型角色池或关键词路由规则。
- 所有 product edits 使用 apply_patch；测试使用已有隔离 fixtures；不读写生产项目做测试，不提交 `output/`，不重启服务作为计划的一部分。

## Review Focus

1. 超长剧本末尾的有效要求不能像当前 `script[:4000]` 一样无标记消失：Task 2/5 测完整来源或显式预算不足。
2. 同 asset ID 的文件被替换，或读取中 Shot 被修改：Task 2/3/5 测来源版本与保存前拒绝，不只测 ID。
3. 伪造 source key、其他项目对象、路径穿越：Task 3 测只读项目边界，不相信模型提交的 project_id/文件路径。
4. 工作包选择错误、反复切换、跨 project 并发回合：Task 4 测可恢复视野、预算不重置、ContextVar 隔离与 finally 清理。
5. 声音参考、尾帧来源、图像内容检查被“精简”省掉：Task 2/5 测必要清单和现有视觉审查调用仍发生，不能用图像文字摘要替代视觉流程。

## 范围锁定与运行方式

现有工作树：`<repository>/.worktrees/director-agent-loop-fix`，分支 `feature/mv-mode`；本计划不要求新建或切换分支。开始执行前重新检查实际 Git 状态；若与基线有新改动，保留并确认重叠范围。

后端命令均在该工作树的 `backend` 目录运行：

```powershell
python -m pytest <test-path> -q
```

下文命令使用 `python -m pytest` 简写；Windows 执行时使用上面的已知解释器。测试数量只在实际运行后报告；前一轮 1539 passed 是基线历史记录，不是本次改动验证。

### 试点开关

新增 `director_task_context_mode: Literal['off', 'shadow', 'pilot'] = 'off'` 和 `director_task_context_projects: list[str] = Field(default_factory=list)`。

- off：原行为，不新增工具或改变模型输入。
- shadow：仅对显式配置的 Director 项目构建新包和记录结构指标；模型仍吃旧输入，不增加推理/生成调用。
- pilot：仅这些项目使用新包。项目列表为空时永远不启用；MV、JSON Production、无 Shot 独立生成走旧路径。
- 环境变量示例只写入文档，不在实现时自动修改用户环境：`DS_DIRECTOR_TASK_CONTEXT_MODE=pilot`、`DS_DIRECTOR_TASK_CONTEXT_PROJECTS=["prj_test_context"]`。
- 回退设置 off；不会回滚已产生的业务操作，也不能替代查外部任务状态。

### 明确不做

不删除现有 pipeline 意图兼容逻辑；不同时修改模型、系统创作指令或 H3 输出格式；不迁移存储；不重写整个 tool schema；不默认让每轮多跑一个路由或评审模型。

新工具仅支持 overview / shot_prompt。其他能力通过 overview 返回现有工具目录，不伪造尚未实现的规划/素材/恢复工作包。保留原来的聊天历史传输与 compaction；本试点不通过删聊天历史追求数字好看。

## 文件职责与接口总表

| 新文件 | 单一职责 |
| --- | --- |
| `backend/app/agents/director/task_context_models.py` | 工作包、来源、查询、临时回合视野与缺口的类型 |
| `backend/app/agents/director/task_context_snapshot.py` | 项目范围内读取并校验当前来源；不推理、不改业务记录 |
| `backend/app/agents/director/task_context_builder.py` | 纯函数选择材料、构建工作包、明确省略/缺口 |
| `backend/app/agents/director/task_context_query.py` | 有版本的只读分页查询和临时视野切换 |
| `backend/app/agents/director/task_context_runtime.py` | 开关、ContextVar 生命周期、现有权限工具集合的展示过滤 |
| `backend/app/agents/director/context_metrics.py` | 无原文的请求形状/长度统计，不估造 token 或记录隐私全文 |
| `backend/app/agents/director/tool_handlers/context.py` | 两个只读工具的参数适配与结果输出 |
| `backend/tests/task_context_fixtures.py` | 合成项目与 fake 调用记录器，无真实模型依赖 |
| `backend/tests/test_task_context_metrics.py` | 观测指标、隐私、开关测试 |
| `backend/tests/test_task_context_builder.py` | 来源充分性、选材、版本/容量边界 |
| `backend/tests/test_task_context_query.py` | 分页、越权、过期、不变更业务状态 |
| `backend/tests/test_task_context_runtime.py` | Legacy/Harness 适配、权限、生命周期、缺口恢复 |
| `backend/tests/test_task_context_writers.py` | 普通/尾帧 writer 共享材料且保留旧契约 |
| `backend/tests/test_task_context_trajectories.py` | 多步真实故障形状、shadow/pilot 对照 |

现有代码修改集中于 config、chat_context、两个聊天 runtime、tool schema/execution、writer 两条路径、调用观测边界。`dialogue_preflight.py` 的语义校验不重写；读取它已验证的结果。

## Task 1: 建立真实请求基线和合成测试底座

**Files:**
- Create: `backend/app/agents/director/context_metrics.py`
- Create: `backend/tests/task_context_fixtures.py`
- Create: `backend/tests/test_task_context_metrics.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/agents/director/llm_plan_provider.py`（三个 complete 方法，guides 注入之后）
- Modify: `backend/app/api/projects.py`（`_make_chat_fn` 内实际 provider 调用之前）

**Interfaces:**
- `context_metrics.request_shape(*, path: str, messages: list[dict], tools: list[dict], image_count: int, source_keys: list[str]) -> dict`
- `context_metrics.record_request_shape(shape: dict) -> None`：通过专用 logger 发出结构化指标；不另建文件数据库。
- `task_context_fixtures.context_case`：pytest fixture，返回 `(project, target, neighbor)`；实际保存到已有 autouse 隔离目录。

- [ ] **1. 写失败测试，证明指标不泄漏原文并统计真实封装。**

```python
from app.agents.director.context_metrics import request_shape

def test_metrics_count_envelope_without_persisting_content():
    shape = request_shape(path="writer", messages=[
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "private-script-秘密"},
    ], tools=[], image_count=2, source_keys=["shot:sht_target"])
    assert shape["message_count"] == 2
    assert shape["text_chars"] == len("SYSTEMprivate-script-秘密")
    assert shape["image_count"] == 2
    assert shape["measurement"] == "characters_not_tokens"
    assert "private-script" not in str(shape)
    assert "秘密" not in str(shape)
```

- [ ] **2. RED。** `python -m pytest tests/test_task_context_metrics.py -q`；新模块不存在导致失败，不能修改已有测试掩盖失败。
- [ ] **3. 实现指标与 fixture，接入已有最终请求边界。**

```python
import hashlib
import json

def request_shape(*, path, messages, tools, image_count, source_keys):
    texts = [m.get("content", "") for m in messages
             if isinstance(m.get("content", ""), str)]
    tool_text = json.dumps(tools, ensure_ascii=False, sort_keys=True)
    return {
        "path": path,
        "message_count": len(messages),
        "text_chars": sum(map(len, texts)),
        "tool_schema_chars": len(tool_text),
        "image_count": image_count,
        "measurement": "characters_not_tokens",
        "input_digest": hashlib.sha256(
            json.dumps([texts, tools], ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        "source_keys": sorted(set(source_keys)),
    }
```

`record_request_shape` 只写上述字段；不记录 message body、base64、source payload 或 API key。实际 provider 有 usage 则可另附真实 usage 并标明来源；没有就不填 token 数。`record_request_shape` 的失败不能破坏生成，日志异常只产生 debug 诊断。

将 config 两个字段按“试点开关”添加；指标在非 off 且 project 被选中时记录。chat 边界记录已组合的 system/history/tools；writer 边界记录 `with_director_skill` 返回的完整请求，不只量 `context_json`。通过本 Task 新建的 `context_metrics.metrics_scope(project_id: str)` ContextVar contextmanager 将 project ID 传到 provider；Task 4/5 在调用入口管理它，off 路径零日志。

Fixture 的核心内容：

```python
import pytest
from app.core.projects.models import Shot
from app.core.projects.store import create_project, save_project, save_shot

@pytest.fixture
def context_case(tmp_projects_dir):
    project = create_project("Context fixture", "A courier waits. A guide arrives.")
    target = Shot(id="sht_target", project_id=project.id, scene_id="scene_a",
                  title="Meeting", script_beat="The guide approaches.", duration_s=6)
    neighbor = Shot(id="sht_neighbor", project_id=project.id, scene_id="scene_a",
                    title="Arrival", script_beat="A courier holds a parcel.", duration_s=6)
    save_shot(neighbor)
    save_shot(target)
    project = project.model_copy(update={"shot_ids": [neighbor.id, target.id]})
    save_project(project)
    return project, target, neighbor
```

- [ ] **4. GREEN 与调用捕获。** fake `client.generate/chat/chat_response` 分别捕获最后 payload，断言指标对应的是注入 guides 后的请求；测试 off 没日志、空项目 allowlist 不启用、MV 不启用。`python -m pytest tests/test_task_context_metrics.py tests/test_harness_runtime.py -q`。
- [ ] **5. 提交。** 仅暂存本 Task 文件；`git diff --cached --check` 后提交 `feat: measure director context envelopes without raw content`。

## Task 2: 带来源的快照与工作包构建器

**Files:**
- Create: `backend/app/agents/director/task_context_models.py`
- Create: `backend/app/agents/director/task_context_snapshot.py`
- Create: `backend/app/agents/director/task_context_builder.py`
- Create: `backend/tests/test_task_context_builder.py`
- Modify: `backend/app/agents/director/chat_context.py`（抽出共用字段序列化，不改变 off 输出）

**Interfaces:**
- `capture_task_snapshot(project_id: str) -> TaskSnapshot`
- `build_task_packet(snapshot: TaskSnapshot, request: TaskRequest, *, authority: dict, max_chars: int, extra_sources: tuple[str, ...] = ()) -> TaskPacket`
- `assert_packet_current(packet: TaskPacket) -> None`：重新读取 packet 的 source manifest，差异抛 `ContextChanged`。
- `task_context_builder.ContextRequired(packet: TaskPacket)`：具有 `code='CONTEXT_REQUIRED'` 和 packet 属性；不属于 PromptFailureError。
- `task_context_snapshot.ContextChanged(ValueError)`：code 为 `CONTEXT_CHANGED`，不伪装素材语义不合格；`assert_packet_current` 也放在此模块。

以下类型在 `task_context_models.py` 定义；禁止用模型提供的 authority 替换后端 authority 参数：

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class SourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    version: str
    trust: Literal["user", "authored", "derived", "execution"]
    payload: dict

class TaskSnapshot(BaseModel):
    project_id: str
    project: dict
    shots: dict[str, dict]
    sources: dict[str, SourceRecord]

class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["overview", "shot_prompt"] = "overview"
    target_shot_id: str | None = None
    objective: str = ""

class TaskPacket(BaseModel):
    schema_version: Literal[1] = 1
    project_id: str
    task: TaskRequest
    authority: dict
    source_versions: dict[str, str]
    facts: dict
    available_context: list[dict] = Field(default_factory=list)
    omitted: list[dict] = Field(default_factory=list)
    missing: list[dict] = Field(default_factory=list)
    complete: bool
```

- [ ] **1. 写选材与不可静默截断的失败测试。**

```python
from task_context_fixtures import context_case
from app.agents.director.task_context_models import TaskRequest
from app.agents.director.task_context_snapshot import capture_task_snapshot
from app.agents.director.task_context_builder import build_task_packet
from app.core.projects.store import save_project, save_shot

def test_prompt_packet_preserves_current_request_and_full_target(context_case):
    project, target, neighbor = context_case
    target = target.model_copy(update={"script_beat": "beat-" * 400})
    save_shot(target)
    packet = build_task_packet(capture_task_snapshot(project.id),
        TaskRequest(kind="shot_prompt", target_shot_id=target.id,
                    objective="Keep the original dialogue; change framing."),
        authority={"allowed_mutations": ["write_prompt"]}, max_chars=50000)
    assert packet.facts["target"]["script_beat"] == target.script_beat
    assert packet.task.objective.endswith("change framing.")
    assert packet.facts["target"]["id"] == target.id
    assert packet.complete

def test_required_script_tail_is_not_silently_truncated(context_case):
    project, target, _ = context_case
    project = project.model_copy(update={"script_text": "x" * 6000 + "FINAL_REQUIREMENT"})
    save_project(project)
    packet = build_task_packet(capture_task_snapshot(project.id),
        TaskRequest(kind="shot_prompt", target_shot_id=target.id),
        authority={}, max_chars=1000)
    assert not packet.complete
    assert any(item["code"] == "CONTEXT_BUDGET_EXCEEDED" for item in packet.missing)
```

- [ ] **2. RED。** `python -m pytest tests/test_task_context_builder.py -q`。
- [ ] **3. 实现读取和纯构建边界。** `capture_task_snapshot` 读取当前 Project/Shot、`directing_request_sources`、当前关联对白来源、selected Layout origin、选中 refs/voices metadata。保留当前字段的完整值；不使用旧 `AgentContext.shot_summaries` 作为权威。

Source keys 使用 `project:<id>`、`shot:<id>`、`script:<project_id>`、`message:<id>`、`asset:<kind>:<id>:<file_key>`、`job:<id>`。这里只是内部定位协议，不接受文件路径。记录原始来源类型；assistant 历史不能成为 user 要求。

图片内容签名复用 `capture_references` 的既有读取路径；缺文件形成 missing，不推理、不生成、不把 asset ID hash 当文件内容 hash。asset 元数据与音频来源也进入相应 version。外部 job 只读取明确 Layout origin/当前 shot 指针关联的 ID，不扫描所有历史生成作为必需输入。

读一次业务源、计算签名，再读一次相关源；最多三次一致性尝试。仍变化则抛 ContextChanged，不返回混合版本。不要在 project lock 内调用模型或外部服务。

区分读取稳定性与提交依赖：读取快照前后比较完整相关业务 payload，排除纯展示时间戳；writer 的 source version 使用当前 `prompt_retry.authored_payload(shot)` 加 `prompt_sections`、`prompt_revision_request`、`prompt_revision_requests`、已验证对白来源和导演要求。系统自己的 material_review_pending、检查缓存和状态回写不作为 authored version，否则 writer 会因自己的前置步骤误判 stale。现有 `check_current()` 的完整原对象检查继续工作，并沿用现有代码在自己合法写入后的 original_shot 更新行为。测试必须覆盖“只改派生审核状态不失效”和“用户改提示词/对白/ref 必须失效”两个方向。

`build_task_packet` 的数据布局固定：`facts.project`、`facts.requirements`、`facts.target`、`facts.continuity`、`facts.dialogue_sources`、`facts.references`、`facts.voice_refs`、`facts.source_audio`、`facts.existing_prompt`、`facts.revision_history`。overview 使用分页目录而非全镜头详情；shot_prompt 含完整 target 与来源，其他 shot 不带完整提示词、job outputs 或 meta。

连续性默认包含相邻镜头的创作摘要和所有选中尾帧的确切 source shot/job；不因邻接自动建立尾帧依赖。没有证据把其他远端镜头当连续性事实时，提供可分页目录，不编造关联。

首次 pilot 不做语义删选全局要求：保留导演来源原文及来源的 script_current 标记、最新请求、目标对白原文。完整剧本优先保留，超预算明确 missing。此选择可能不减少长剧本的输入量，报告这个限制；不凭关键词猜“哪句话不重要”。目标任务的其他大详情可按来源查询，available_context 使用计数/分页描述，不枚举成千上万个对象。

容量打包的关键逻辑：

```python
import json

def packet_fits(packet, max_chars):
    return len(json.dumps(packet.model_dump(mode="json"), ensure_ascii=False)) <= max_chars

def require_complete(packet):
    if not packet.complete:
        raise ContextRequired(packet)
    return packet
```

在 builder 定义上述 `packet_fits` / `require_complete`；必需材料超过预算时返回小型不完整包，missing 包含超预算来源 key、版本、字符量和 read 定位，不返回超过预算的完整 body，也不能截掉 body 后 complete=true。预算不是 token 上限证明，最终调用仍走现有 provider 容量/媒体预留与 compaction 检查。单纯分页读完长剧本不能解决 writer 容量不足；此时明确要求任务缩小或更大上下文，不自动拼回超限请求。

- [ ] **4. GREEN 与边界测试。** 补测试：无对白、已有 dialogue_grounding、Voice-only/源音频、同名 Shot 必须按 ID、500 个无关 shot 的详情不进入 prompt packet、尾帧 source_job_id 保留、当前修改覆盖旧摘要、构建期间改 ref、相同 ID 换图片。检查读取前后所有业务文件内容哈希相同。`python -m pytest tests/test_task_context_builder.py tests/test_dialogue_camera_revision.py tests/test_reference_facts.py -q`。
- [ ] **5. 提交。** `feat: build versioned director task context packets`。

## Task 3: 可追溯的只读补查与临时视野

**Files:**
- Create: `backend/app/agents/director/task_context_query.py`
- Create: `backend/app/agents/director/task_context_runtime.py`
- Create: `backend/tests/test_task_context_query.py`
- Modify: `backend/app/agents/director/task_context_models.py`

**Interfaces:**
- `ContextRead(source_key: str, expected_version: str | None = None, offset: int = 0, limit: int = 2000)`；offset≥0，1≤limit≤8000，extra forbid。
- `ContextPage(source_key: str, version: str, trust: Literal['user','authored','derived','execution'], format: Literal['text','json'], offset: int, next_offset: int | None, total_chars: int, text: str, truncated: bool)`。
- `TaskContextState(project_id: str, request: TaskRequest, retrieved_versions: dict[str,str], context_epoch: int = 0, read_pages: set[tuple[str,str,int,int]] = Field(default_factory=set))`；在 models 模块实现为可变 Pydantic 模型，ephemeral，不写 JSON。
- `read_task_context(state: TaskContextState, query: ContextRead) -> ContextPage`
- `set_task_context(state: TaskContextState, request: TaskRequest) -> TaskContextState`
- `task_context_scope(state: TaskContextState)`：ContextVar contextmanager，退出时 reset token；`current_task_context(project_id: str) -> TaskContextState | None` 严格匹配 project。
- `task_context_enabled(project) -> bool`：应用 config mode、project allowlist 和 Director mode。

- [ ] **1. 写分页与越权失败测试。**

```python
import pytest
from task_context_fixtures import context_case
from app.agents.director.task_context_models import ContextRead, TaskContextState, TaskRequest
from app.agents.director.task_context_query import read_task_context

def test_read_is_versioned_and_paged(context_case):
    project, target, _ = context_case
    state = TaskContextState(project_id=project.id,
        request=TaskRequest(kind="shot_prompt", target_shot_id=target.id),
        retrieved_versions={})
    first = read_task_context(state, ContextRead(source_key=f"script:{project.id}", limit=8))
    assert first.text == project.script_text[:8]
    assert first.next_offset == 8
    assert first.truncated
    assert state.retrieved_versions[first.source_key] == first.version

def test_source_path_is_not_a_query(context_case):
    project, _, _ = context_case
    state = TaskContextState(project_id=project.id, request=TaskRequest(), retrieved_versions={})
    with pytest.raises(ValueError, match="CONTEXT_SOURCE_NOT_ALLOWED"):
        read_task_context(state, ContextRead(source_key="../../backend/.env"))
```

- [ ] **2. RED。** `python -m pytest tests/test_task_context_query.py -q`。
- [ ] **3. 实现受限读取。** 从 `state.project_id` 的当前 source catalog 解析 source key；不从 key 直接拼路径。asset 必须被该项目引用/明确归属，job 必须属于项目且在被查询目标关系内。read 只返回文本/元数据；图像仍通过既有 inspect/vision 路径，不把 base64 灌进文本查询。

将 `catalog:shots` 作为特殊项目目录来源，payload 是有序 ID/title 索引，按页查询；读取目录本身不让所有 shot 变成创作依赖。真正取得详情的来源才进入 retrieved_versions。读取 message 时保留 role、原文、来源 ID；不知道 message ID 时可分页查询 `catalog:messages`（ID/role/短预览/截断标记），不新增全文语义检索服务。

分页版本规则：首次可不带 expected_version；后续带该版本，源内容变化返回 CONTEXT_CHANGED，不继续拼混合版本。版本不匹配时不更改 state.retrieved_versions；成功后增加 context_epoch。幂等重读同页不算新的信息，epoch 只在首次取得或取得新版本/新页时增加；在 state 额外保存已读 `(key, version, offset, limit)` 集合。

script 返回原始文本并标记 format=text；其他对象返回 canonical JSON 文本并标记 format=json。message 的 JSON 保留 role 和 source ID，ContextPage.trust 不因内容中自称“用户批准”而改变。分页 JSON 的单页不保证是完整 JSON 对象，消费者按 offset 组装同版本页面，不能逐页强制解析或拼接不同版本。

```python
def page_text(text, offset, limit):
    end = min(len(text), offset + limit)
    return text[offset:end], (end if end < len(text) else None)
```

`set_task_context` 仅改变 kind/target：验证 project 拥有目标后更新 request，保留最初 objective、retrieved_versions 和已读集合。模型不能通过工具传入 objective 覆盖最新用户原话。切到 overview 时不擦除失败记录；此模块不持有业务执行预算，不可能 reset 它。

- [ ] **4. GREEN。** 补测试其他项目 shot/job、无归属 Library、未知 source、负 offset、超 limit、message.role 保留、查询中 source 改变、同页重复 epoch 不变、切换后授权/预算引用不变、两个异步 scope 无串扰。`python -m pytest tests/test_task_context_query.py tests/test_task_context_builder.py -q`。
- [ ] **5. 提交。** `feat: add scoped read-only task context lookup`。

## Task 4: 将工作包接入两种 Agent，保持权限和预算

**Files:**
- Create: `backend/app/agents/director/tool_handlers/context.py`
- Create: `backend/tests/test_task_context_runtime.py`
- Modify: `backend/app/agents/director/task_context_runtime.py`
- Modify: `backend/app/agents/director/tool_schema.py`
- Modify: `backend/app/agents/director/tool_execution.py`
- Modify: `backend/app/agents/director/harness_runtime.py`
- Modify: `backend/app/agents/director/chat_orchestrator.py`

**Interfaces:**
- `render_task_context(project, *, objective: str, authority: dict, legacy_state: str, max_chars: int) -> str`：off 返回原字符串；shadow 构建/统计后仍返回原字符串；pilot 返回 packet JSON。
- `present_task_tools(authorized_tools: list[dict], state: TaskContextState) -> list[dict]`：只过滤现有后端授权集合，不能创建新的业务权限。
- `handle_context_tool(*, name: str, args: dict, project_id: str, result_payloads: list[dict]) -> bool`：只认两个工具，输出结果，不追加业务成功 action 或 touched shot。
- 两个模型工具：`set_task_context({kind, target_shot_id?})`、`read_task_context({source_key, expected_version?, offset?, limit?})`。

- [ ] **1. 写开关、授权和生命周期失败测试。**

```python
from task_context_fixtures import context_case
from app.agents.director.task_context_models import TaskRequest, TaskContextState
from app.agents.director.task_context_runtime import present_task_tools

def test_switch_does_not_grant_new_mutations(context_case):
    project, target, _ = context_case
    allowed = [{"type": "function", "function": {"name": "write_prompt",
                "parameters": {"type": "object", "properties": {
                    "shot_id": {"const": target.id}}, "required": ["shot_id"]}}}]
    state = TaskContextState(project_id=project.id,
        request=TaskRequest(kind="shot_prompt", target_shot_id=target.id),
        retrieved_versions={})
    offered = present_task_tools(allowed, state)
    names = {item["function"]["name"] for item in offered}
    assert "revise_shot" not in names
    assert "start_h3_video" not in names
    prompt = next(t for t in offered if t["function"]["name"] == "write_prompt")
    assert prompt == allowed[0]
    assert {"read_task_context", "set_task_context"} <= names
```

- [ ] **2. RED。** `python -m pytest tests/test_task_context_runtime.py -q`。
- [ ] **3. 接入现有循环，不新增循环。** 自由聊天初始 overview，不解析用户关键词选模式；现有明确 `write_prompt(shot_id)` 或 scoped retry 入口为对应目标构建包。set 工具只允许两个 kind。shot_prompt 展示 write_prompt/get_status/inspect_asset 与两个 context 工具；overview 展示调用方原本允许的完整工具集合，保留 exact-shot schema。

`present_task_tools` 不在上传待分类或 terminal_failure 时调用；继续保持这些状态的原有工具封锁。managed scope 和 material review 允许只读补查，但业务 schema 不扩大。已有 script lock、storyboard budget、pipeline sanitizer 仍执行。

Harness `BackendTurn.context()` 在调用原构建器之后经过 render 适配；tool admission 增加两个只读工具，放入 read-only 去重分类，仍计入相同 call_ids/tool budget。refresh context 不更新 expected_state 来掩盖并发变更；只在新的真实 inference 建立有效快照。Legacy 的首次与后续推理都从同一 renderer 取得状态；在现有 `_filter_tools_to_offered_schemas` / sanitizer 中识别两个只读工具，不能因此跳过其他校验。

两个聊天运行时入口用同一个 scope 包住整个回合，finally reset。writer 的嵌套复用与直接调用 scope 在 Task 5 的 service 入口实现。已有 chat_fn 的 system 封装、图像注入、prepared_system、compaction 不改变。metrics_scope 使用相同生命周期。

```python
from contextlib import contextmanager
from contextvars import ContextVar

_task_state = ContextVar("director_task_context", default=None)

@contextmanager
def task_context_scope(state):
    token = _task_state.set(state)
    try:
        yield state
    finally:
        _task_state.reset(token)

def current_task_context(project_id):
    state = _task_state.get()
    return state if state is not None and state.project_id == project_id else None
```

scope 具体实现留在 Task 3 文件；Task 4 只接调用，不能创建第二个 ContextVar。backend 生成的 authority 包含原业务工具 schema 摘要和 locked/scope 状态，但实际授权继续由现有执行边界判断。

max_chars 在调用边界从现有 context_capacity 与输出预留预算折算，沿用 `harness_input_budget` 的现有估算口径，并扣除本次 system、历史、工具 schema 和媒体预留；传给 builder 的值明确是估算字符预算，不是保证可用的精确 token 数。不能直接把整个模型窗口都分配给 task packet。已知预算≤0 时构建缺口结果，不发起一次注定超限的 writer 请求；单元测试显式传入预算以保持可复现。

- [ ] **4. GREEN 与双运行时轨迹。** mock native model 输出依次 set_task_context → read_task_context → get_status → final；检查每次 provider 输入与 source manifest。失败/取消退出后 current_task_context 为 None。测 off 工具清单完全保持旧测试预期，shadow 无第二次模型调用，pilot 空 allowlist 不生效，task 切换后全局预算没有增加。

Run: `python -m pytest tests/test_task_context_runtime.py tests/test_harness_runtime.py tests/test_harness_grounding_contract.py tests/test_director_dialogue_attribution.py -q`。
- [ ] **5. 提交。** `feat: wire task context views into director agent runtimes`。

## Task 5: 普通/尾帧 writer 使用同一来源工作包

**Files:**
- Create: `backend/tests/test_task_context_writers.py`
- Modify: `backend/app/agents/director/task_context_builder.py`
- Modify: `backend/app/agents/director/service.py`
- Modify: `backend/app/agents/director/tail_prompt_review.py`
- Modify: `backend/app/agents/director/tool_handlers/layout.py`
- Modify: `backend/app/agents/director/harness_runtime.py`
- Modify: `backend/app/agents/director/chat_orchestrator.py`
- Modify: `backend/app/agents/director/prompt_retry.py`

**Interfaces:**
- `writer_task_context(packet: TaskPacket, *, dialogue_lines: list[dict], reference_evidence: list[dict]) -> dict`：返回同一来源事实；不生成创作描述，不写数据。
- `ContextRequired` 用 Task 2 异常；对应工具 payload：`{ok:false, code:'CONTEXT_REQUIRED', shot_id, missing, available_context, concludes_turn:false}`。
- `assert_packet_current` 用 Task 2 实现，在现有 `check_current()` 内叠加检查补查来源，不替代原检查。

- [ ] **1. 写 writer 输入一致性与不绕过证据的失败测试。** 使用现有 `material_shot` / `tail_handoff_shot` 与 fake Provider；不新建真人影片测试。

```python
import pytest
from app.agents.director.task_context_builder import writer_task_context, ContextRequired

def test_incomplete_packet_never_becomes_writer_input():
    from app.agents.director.task_context_models import TaskPacket, TaskRequest
    packet = TaskPacket(project_id="p", task=TaskRequest(), authority={},
        source_versions={}, facts={}, complete=False,
        missing=[{"code": "CONTEXT_BUDGET_EXCEEDED", "source_key": "script:p"}])
    with pytest.raises(ContextRequired):
        writer_task_context(packet, dialogue_lines=[], reference_evidence=[])
```

- [ ] **2. RED。** `python -m pytest tests/test_task_context_writers.py -q`。
- [ ] **3. 先检查完整性，再调用现有 writer。** 对 pilot 路径，在 `service._write_prompts_after_layout_impl` 做不产生业务写入的包完整性检查，必须早于写 material_review_pending。然后继续原有 capture/review/prepare_dialogue/compile/validate/certify/save 流程。

`service.write_prompts_after_layout` 若当前已有同 project 的 task_context_scope/metrics_scope 则复用；否则建立目标明确的临时 scope 并在 finally reset。off 或 allowlist 外走原路径。直接调用缺少聊天 history 时按实际 writer system/user/guides 和现有输出预留预算计算，不虚构不存在的消息；嵌套 writer 不把外层聊天历史重复塞进 writer 请求。

正常 writer 的 `context_json` 用经过 require_complete 的 writer_task_context 替换旧 AgentContext 大摘要。模板中原有 target/refs/voices/directing requirements 等字段先保留，不能为减少重复一次性改全部模板；实际请求统计会显式显示剩余重复成本。新包中已有来源字段可在随后单独通过回归的微提交去重，不把尚未量化的收益写入报告。

tail writer 保留 `DRAFT_INSTRUCTIONS`、candidate envelope、compatibility audit 和双次候选预算；request 增加相同 canonical task context，并让原 `original_shot/script/references/dialogue_lines/voice_refs/revision_history` 从同一包与既有已验证 review 结果取得。该次 material review 若合法生成候选 brief，候选与原始 source snapshot 分开：packet 保留原版本，candidate 字段明确标为派生提案；不能把候选冒充持久化新规格。

`writer_task_context` 的最小编排：

```python
def writer_task_context(packet, *, dialogue_lines, reference_evidence):
    require_complete(packet)
    return {
        "schema_version": packet.schema_version,
        "task": packet.task.model_dump(mode="json"),
        "source_versions": packet.source_versions,
        "authority": packet.authority,
        "facts": packet.facts,
        "verified_dialogue_lines": dialogue_lines,
        "reviewed_references": reference_evidence,
    }
```

普通和尾帧 writer 不直接调用 read 工具、不解析新工具标记、不增加独立模型循环。缺材料/超预算由后端返回 ContextRequired 给外层 Agent；真正语义歧义仍走已有 CreativeQuestion，不把两类错误混同。

特判顺序：layout handler 在通用 Exception 之前捕获 ContextRequired，返回上面的非终态 payload。`service.write_prompts_after_layout` 和 scoped retry 不将它登记成“生成候选已失败”的 prompt retry，不消耗候选修复预算。Harness/Legacy 对 `code == 'CONTEXT_REQUIRED'` 不设置 terminal_failure/PROMPT_GENERATION_FAILED，允许有界只读补查或解释问题；其他真实 prompt failure 行为不变。

防止绕过去重：同一输入、同一缺口且无新来源时，重复 write_prompt 不执行。允许重试只在缺口对应来源已取得/刷新且 require_complete 通过时，使用该 packet source_versions 的 digest 区分新的安全 preflight；这只适用于之前尚未执行生成的 CONTEXT_REQUIRED 回执，不能对已提交或结果未知的 mutation 放开去重。任意 set_task_context 或重复读取同页都不能解除此限制。

直调 writer 没有外层 Agent 时返回明确 ContextRequired，不自动进入聊天循环。现有 run 仍可按原流程暂停并显示具体缺口；本阶段不更改 managed 调度和恢复预算。小型有效项目应正常通过；超容量项目不能因来回分页而误判可提交。

- [ ] **4. GREEN。** fake provider 检查普通和尾帧包含相同台词来源、Picture 编号和 Voice 映射，保留确切 tail source_job_id。记录 visual 调用仍覆盖所有选中图片。输入中途变化导致保存拒绝；scoped retry 仍不改 authored fields；ContextRequired 没有 material-review/job/shot 写入；缺口补查成功只执行一次 writer；重复查询和切任务不产生第二次生成。

Run: `python -m pytest tests/test_task_context_writers.py tests/test_director_material_review.py tests/test_tail_dialogue_serialization.py tests/test_tail_prompt_review.py tests/test_scoped_prompt_retry.py tests/test_dialogue_camera_revision.py tests/test_task_context_runtime.py -q`。
- [ ] **5. 提交。** `refactor: share task evidence across director prompt writers`。

## Task 6: 多步回归、质量对照和试点交付

**Files:**
- Create: `backend/tests/test_task_context_trajectories.py`
- Keep verification evidence in an ignored local directory.
- Modify: `docs/HARNESS.md`（只增加上下文试点选项与限制）

**Interfaces:** 沿用 Task 1—5，不引入新业务接口。报告中的真实模型部分在未授权/未执行时标记“未执行”，不是通过。

- [ ] **1. 写行为轨迹失败测试，不依赖唯一黄金 prompt。**

```python
from task_context_fixtures import context_case
from app.agents.director.task_context_models import TaskRequest, TaskContextState, ContextRead
from app.agents.director.task_context_query import set_task_context, read_task_context
from app.agents.director.task_context_snapshot import capture_task_snapshot
from app.agents.director.task_context_builder import build_task_packet

def test_wrong_focus_can_be_corrected_without_changing_authored_state(context_case):
    project, target, neighbor = context_case
    before = capture_task_snapshot(project.id)
    state = TaskContextState(project_id=project.id,
        request=TaskRequest(kind="shot_prompt", target_shot_id=neighbor.id,
                            objective="Rewrite the requested target prompt only."),
        retrieved_versions={})
    read_task_context(state, ContextRead(source_key=f"shot:{target.id}"))
    state = set_task_context(state, TaskRequest(kind="shot_prompt", target_shot_id=target.id))
    packet = build_task_packet(capture_task_snapshot(project.id), state.request,
        authority={"allowed_mutations": ["write_prompt"]}, max_chars=50000,
        extra_sources=tuple(state.retrieved_versions))
    assert packet.task.target_shot_id == target.id
    assert packet.task.objective == "Rewrite the requested target prompt only."
    assert capture_task_snapshot(project.id) == before
```

- [ ] **2. RED。** `python -m pytest tests/test_task_context_trajectories.py -q`；轨迹如果直接通过，补充尚未覆盖的 stale/ref-change/缺口动作序列，而不是制造无意义失败。
- [ ] **3. 补齐集成缺口，保留阶段范围。** 本 Task 的完整场景矩阵：

| 场景 | 断言 |
| --- | --- |
| 恢复台词 → 改运镜 → 再写提示词 | 同一来源/说话人仍有效，不重复提取 |
| 植入旧服装叙述和新 ref | 当前选择/原始证据进入工作包，旧摘要不替代它 |
| 500 个无关 shots + 长聊天 | prompt 包不携带无关详情；最终请求仍含历史时如实记录，不声称总 token 已降低 |
| Agent 补查后 UI 换 ref | 保存前 source 校验拒绝旧内容，保留新选择 |
| scoped retry 切回 overview | 只改变视野，不能获得 revise_shot/start_h3_video 权限 |
| 相同输入两种合理运镜/表演 | 两者都可通过原有结构契约，不依靠固定 prose |
| shadow / off / pilot | shadow 与 off 的 provider 调用数、业务写入一致；pilot 只影响 allowlist 项目 |
| pilot 中取消聊天 | scope 清理；不取消已提交且独立运行的生成 Job |

只修 Task 1—5 的集成缺口；若发现 P1B 状态重构才可解决的独立问题，记录并停止扩展，不在本 Task 开始迁移状态机。

- [ ] **4. GREEN 与完整验证。**

```powershell
python -m pytest tests/test_task_context_metrics.py tests/test_task_context_builder.py tests/test_task_context_query.py tests/test_task_context_runtime.py tests/test_task_context_writers.py tests/test_task_context_trajectories.py -q
python -m pytest -q
```

在 frontend 目录运行 `npm test`、`npm run build`，确认 API/type 兼容。执行 `git diff --check`，检查没有真实项目文件、媒体、环境密钥进入 diff。

报告记录：实际 commit、命令与退出结果；每条轨迹结果；新旧输入的系统/历史/工具/任务材料字符量和图像数；字段级必要来源覆盖；补查与切换次数；没有实测的性能/语义收益明确不宣称。

真实模型对照需用户指定或批准测试项目与成本范围后执行：保持同模型/采样设置，使用相同最小输入样本，每组至少三次重复，保留成功、误拦、遗漏、恢复结果和实际 usage。三次只是小样本观察，不是统计显著性证明；样本存在新增关键遗漏或误提交即不推广。未做对照时保持默认 off，不自动修改项目 allowlist。

- [ ] **5. 提交与交付。** `test: verify director task context pilot trajectories`。报告未完成的实测，不把全部单元测试通过等同于生产质量提升。最终交付包含可回退开关、测试结果和 P1B 是否具备进入条件，不自动继续 P1B。

## 自审与规格覆盖

| 设计要求 | 本计划落点 |
| --- | --- |
| 任务划分而非多 Agent | Task 2/4，仅 overview / shot_prompt 与既有运行时 |
| 公共底座、完整证据、按需查询 | Task 2/3/5，带版本与缺口标记 |
| 动态选择任务而非关键词路由 | Task 4，同一个 Agent 选择工具，无额外路由推理 |
| 不扩大权限、不重置预算 | Task 3/4/5 与 Review Focus 4 |
| 快照一致、旧来源不能保存 | Task 2/3/5，复用旧保护并叠加 source manifest |
| 普通/尾帧与 Legacy/Harness 共享事实 | Task 4/5，保留各自创作策略与输出协议 |
| 只读比较、可回退、默认不影响生产 | Task 1/4/6，项目 allowlist 与 off/shadow/pilot |
| 质量而非 token 数验收 | Task 6，同条件多样本评估与未执行声明 |
| 规格中的状态/数据库/托管重构 | 明确属于 P1B—P4，未在本计划实现或宣称完成 |

编写时已核对新类型与接口命名、Task 依赖、作用范围与六类现有入口；已明确自身派生状态写入不引发误失效、外层 Agent 与一次性 writer 的能力差异、长剧本预算不足不靠无限分页掩盖。执行前重新核对实际代码基线。实施时若发现必须改变上述接口或扩大职责才能继续，先报告具体冲突，不把开发过程中形成的新架构决定静默并入本计划。

## 执行方式选择

推荐 Native：由当前会话按六个 Task 顺序实施，每个 Task 做 RED/GREEN 与小提交，最后做整体验证及独立审查。理由是快照、scope、writer 的接口高度相关，先避免多个实现者同步修改相同 runtime/service 文件。

也可选择 Subagent-driven：每个 Task 由独立实现者和审查者处理，按依赖串行推进；独立性更强，但交接与上下文成本更高。无论哪种方式，都不能在本清单获批前开始实现。
