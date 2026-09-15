# Qwen3.8 27B：Harness / legacy 真实模型测试

## 结论

**目前不适合把这版 Harness 设为默认。** 长历史保留与 stock compaction 确实有价值，但接入层仍有首次超窗、重复上下文、工具结果膨胀、参数校验不一致和批量调用预算问题。失败不能统一归因为「Qwen 不会收敛」，也不能认为「接上 Harness 就自动解决」。

本轮只测试、定位原因，没有修改生产运行时代码。所有业务写入均在隔离评测目录，没有生成图片、音频或视频，没有改动用户项目或全局模型选择。

## 条件与判定

- 时间：2026-09-12，America/Los_Angeles。
- 模型：用户当前选用的 `qwen3.8:27b`，27.3B，Q4_K_M。
- Ollama 0.33.3；RTX 4090 24 GB；实际 GPU 驻留推理。
- 默认 `num_ctx=32768`、`num_predict=4096`；沿用模型默认采样参数（temperature=1、top_k=20、top_p=0.95），统一 seed=42。
- 使用实际 `_make_chat_fn`、Python provider/VRAM 路径、真实工具处理器和真实 Node Harness/stock compactor。分镜保存时的后端模型校验也实际运行，并计入调用和耗时。
- 8 个成对场景 × 2 个 runtime，加 2 次诊断对照，共 **18 次端到端运行**；另有 4 次只推理、不执行工具的原始/精简请求重放。
- 初始预检 4 次不计入正式结果：编辑夹具最初漏写「已规划版本」标记，引发 legacy 自动重规划；同时修正评测输出的 Windows 编码问题。正式编辑夹具包含真实 AgentContext，明确列出要修改的字段。
- 成功必须同时满足：真实保存结果正确、镜头数量/ID/顺序正确、未指定镜头不变、指定镜头其他创作字段不变，以及正常返回最终结果。**保存成功但最后一轮报错，单独注明，不能当作没有保存。**
- 这是定向复现、小样本基准，**不能据此估计真实使用中的统计失败率**。项目 ID 和自然语言生成会有差异，秒数是本次观测值。

## 默认配置结果

| 场景 | legacy | Harness |
|---|---|---|
| 4 镜中精确改 1 镜标题＋时长 | 通过；8.2 秒、2 次模型调用 | 失败；23.9 秒、6 次调用，参数类型反复不合法 |
| 创建 12 镜，每镜 5 秒 | 通过；71.3 秒、3 次调用 | 通过；75.4 秒、3 次调用 |
| 创建 24 镜，每镜 5 秒 | 通过；64.9 秒、3 次调用 | 失败；53.1 秒，输出截断，0 镜保存 |
| 24 镜中精确改 3 镜标题＋时长 | 通过；15.2 秒、2 次调用 | 失败；53.4 秒、6 次调用，只有部分修改 |
| 24 镜中改 3 镜，仅标题 | 通过；13.6 秒、2 次调用 | 通过；21.7 秒、2 次调用 |
| 82 条历史＋24 镜，改 3 镜并遵守早期标题后缀约定 | 失败；早期约定未进入请求 | 通过；52.0 秒，3 次调用，其中一次真实 compaction |
| 162 条历史＋24 镜，同一记忆任务 | 失败；约定丢失，虽然修改了标题但后缀不对 | 失败；首次请求耗尽窗口，0 镜修改 |
| 一次修改全部 24 镜标题 | **24/24 已保存**，随后模型请求 HTTP 500，最终返回失败 | **12/24 已保存**，另外 12 个触及工具上限；随后 HTTP 500 |

长历史夹具包含已结束的制作讨论，关键「【蓝灯】」后缀约定仅在早期出现。82 条共 61,206 字符；162 条共 122,470 字符。它们同时检验历史保留与上下文压力，不能视为两边输入完全相同：legacy 实际只保留最近 4 条、每条最多 400 字符。

## 已定位的问题与对照证据

### 1. 工具结果重复携带整张分镜表，批量工作后迅速变大

每次 `revise_shot` 都返回全体 shots 的 `storyboard`。连续改 N 镜时，结果中重复 N 次全表，输入规模随镜头数量和操作数量相乘增长。

24 次修改后的下一轮请求：

| 请求 | 原始请求字符数 / 状态 | 只移除重复 storyboard 字段后 |
|---|---|---|
| legacy | 277,659；HTTP 500 `no user query found in messages` | 49,395；HTTP 200；12,877 输入 token，正常确认已保存 24 镜 |
| Harness | 159,334；同样 HTTP 500 | 52,786；HTTP 200；12,944 输入 token，正确说明仅前 12 镜成功 |

两份原始请求都通过同一 HTTP 客户端重放并再次得到 500；精简请求保留所有消息、工具调用、成功/错误标志与 notes，仅删除重复 `storyboard`。重放没有执行工具。原请求实际含 user 消息，因此不能把错误字面理解成应用根本没发送 user。**结果膨胀是已验证的触发因素；服务内部究竟截掉哪一段，未启用额外 debug 跟踪。**

涉及 `backend/app/agents/director/tool_handlers/project.py` 的 `revise_shot` 结果、`chat_orchestrator.py` 的工具反馈拼接，以及 Harness 对工具结果的原样回传。

### 2. 首次请求之前没有压缩；窗口耗尽没有走 overflow 恢复

82 条历史：第一次正常推理 26,775 输入 token，正确取回后缀并修改 3 镜。第二次调用为 `purpose=compaction`，18,608 输入 token；压缩后的第三次调用 19,014 输入 token，正常完成。

162 条历史：**第一次调用就达到 32,682 输入 token，只产出 83 token，`done_reason=length`，没有工具调用，也没有任何 compaction 调用。** 这与创建 24 镜的 4,096 输出上限问题不是同一种截断。

源码与记录一致：stock compactor 的 `compactIfNeeded()` 需要 `session.requestHeader()` 中的 routed target；当前每轮新建的会话只种入文字历史，没有之前的 routed request header。其 context-overflow 路径又要求 `CONTEXT_WINDOW_EXCEEDED` 错误，而此次 Ollama 返回的是成功 HTTP 响应里的 `done_reason=length`，被适配器当作普通截断结束。

因此，已有长历史在**新一轮的第一步**就可能失败，后续压缩根本没有启动机会。

### 3. 同一运行上下文被放入请求两遍

Python system 已加入权威 PROJECT_STATE；Node 的 `systemPrompt.context` 又生成一条 `role=user` 的 `Current runtime context...` 消息，Python 只丢弃了 Node 的 system 消息，没有去掉这份重复内容。

同一短编辑任务第一次输入：legacy **7,970 token**，Harness **10,834 token**。24 镜编辑第一次输入：legacy **9,927**，Harness **14,764**。这不是能力差异，而是接入开销；后续快照与全表工具结果还会继续增加压力。

### 4. 数字字符串在两个 runtime 中得到不同处理

真实 Qwen 工具输出包含 `duration_s: "7"`。现有 `ShotRevisionSubmission.model_validate()` 会接受并归一为 `7.0`；新增 `Draft202012Validator` 拒绝相同输入。已独立验证这两个校验器的行为差异。

模型在 Harness 中反复提交 `"7"` / `"7.0"`，没有可靠修复。只改标题的匹配对照中，两边都通过。因此含时长的失败不能归因于「24 镜太多」或「模型看不懂修改意图」。

### 5. 12 步与 12 个工具调用混用，限制合法批量任务

一次重命名 24 镜的真实响应包含 24 个 `revise_shot` 调用。Harness 后端执行了前 12 个，后 12 个全部返回 `Turn tool limit reached`。这是 `harness_max_steps` 同时被用作推理步数和工具调用数的确定性限制，不是模型忘记做完。

取消重试保护与正常批量操作预算需要分开设计。不能仅把所有预算无限调大，也不能指望模型自行突破硬上限。

### 6. 多镜创建还受到输出上限影响

默认 Harness 创建 24 镜：输入 12,494 token，输出 **4,096** token 后截断，没有完整工具调用。

隔离对照只把 `num_predict` 提高到 **8192**，同一模型与场景成功保存 24 镜，首次输出 **4,275** token，总耗时 72.0 秒。这支持输出预算不足的诊断；该配置仅作用于评测进程，没有更改用户 `.env`。

另一个信息保留对照：legacy 的 82 条历史案例失败后，在当前请求重述「【蓝灯】」约定，即通过同样的 3 镜修改，17.0 秒。它原先失败是因为早期约定被裁掉，不能拿它较短的输入和响应时间当作长上下文处理优势。

## 建议的最小修复顺序（初测时尚未实施）

1. **先精简工具结果**：单镜修改返回该镜头的修改结果/必要差异，不在每次调用中返回全表；完整状态走显式读取。
2. **补首次请求的上下文预算检查**，预留输出空间；区分输入窗口耗尽与输出上限，并将真实 usage/错误信息传回 Harness。
3. **只保留一份权威运行上下文**，不要同时注入 Python system 与 Node synthetic user snapshot。
4. **统一现有业务模型的类型归一化与校验**，避免新增 JSONSchema 校验和既有 Pydantic 行为不一致；保留权限、当前状态及副作用防重放约束。
5. **把推理步数、工具数量和批量更新预算分开**；对多 shot 请求采用有界批次或明确的批量工具契约，再评估输出预算。

随后原样复跑这些夹具。重点是「指定修改全部落盘、邻镜不变、最后能正常返回」，不是只看 agent 是否给出了流畅解释。

## 原始证据与复跑

脚本：`scripts/evaluate_harness_live.py` 与 `scripts/probe_harness_tool_results.py`。每个案例保存 fixture、每次模型请求/响应、调用指标、工具反馈及修改后的 shots。

- [默认配置 8 次结果](../../.tmp/harness-live-20260912/scored-defaults-084141/summary.json)
- [长历史与批量 8 次结果](../../.tmp/harness-live-20260912/context-batch-084750/summary.json)
- [8192 输出预算对照](../../.tmp/harness-live-20260912/output-budget-control-085219/summary.json)
- [legacy 重述早期约定对照](../../.tmp/harness-live-20260912/history-retention-control-085442/summary.json)
- [legacy 原始请求重放](../../.tmp/harness-live-20260912/context-batch-084750/edit24_all-legacy/original-tool-result-probe.json) / [精简结果重放](../../.tmp/harness-live-20260912/context-batch-084750/edit24_all-legacy/compact-tool-result-probe.json)
- [Harness 原始请求重放](../../.tmp/harness-live-20260912/context-batch-084750/edit24_all-harness/original-tool-result-probe.json) / [精简结果重放](../../.tmp/harness-live-20260912/context-batch-084750/edit24_all-harness/compact-tool-result-probe.json)

从仓库根目录、Ollama 已启动且 Comfy 无排队任务时运行：

```powershell
python scripts/evaluate_harness_live.py --cases edit4_short,create12_short,create24_short,edit24_short --timeout 600
python scripts/evaluate_harness_live.py --cases edit24_short,edit24_long,edit24_overflow,edit24_all --title-only --runtimes harness,legacy --timeout 600
python scripts/evaluate_harness_live.py --cases create24_short --runtimes harness --predict 8192 --timeout 600
python scripts/evaluate_harness_live.py --cases edit24_long --runtimes legacy --title-only --repeat-constraint --timeout 600
```

本次采用系统化排查与 convergence-guard：预检夹具错误不混入正式结果，遇到失败先做窄对照，不在评测中暗改生产实现或无限追加重试。

## 后续修复：单镜修改结果精简（2026-09-12）

本轮仅实施第 1 项中的 `revise_shot`：返回 `{ok: true, shot: 已保存单镜快照}`，
不再返回完整 `storyboard`。两个 runtime 共享此处理器。磁盘数据、项目读取、
最终聊天状态、失效 prompt/H3 清理及错误路径不变；其他工具结果契约未改。
未修改默认模型、上下文/输出预算、参数校验或 Harness 的 12 次工具限制。

沿用同一 Qwen3.8:27b、seed 42、32768 上下文、4096 输出配置和隔离夹具，
只改标题以避开已知数字字符串校验问题，每格一次，不代表统计失败率：

| 案例 | legacy | Harness |
|---|---|---|
| 修改全部 24 镜标题 | 通过，24/24 保存并正常回复，25.4 秒（包含冷启动预热） | 未通过，12/24 保存，55.4 秒；无 HTTP 500，最终明确报告剩余 12 镜未完成 |
| 修改 24 镜中的 3 镜标题 | 通过，10.2 秒 | 通过，20.1 秒 |

legacy 全 24 镜修改后的模型请求由初测的 **277,659 字符降到 59,159 字符**
（约减少 79%），实际输入 15,937 token，正常完成。Harness 首批工具后的请求
为 72,878 字符、18,450 token，也正常返回，但模型随后仍重复尝试被预算拒绝的
修改；共 5 次推理后报告未完成。这说明精简消除了本次复测中的超大工具回包错误，
**没有解决合法批量操作被 12 次调用上限拦截的问题，也不能据此宣布长历史已修复**。

验证：新增 4/24 镜真实存储测试先失败于完整表回包，修改后通过；缺失目标仍返回失败。
相关 Python 回归 **368 项通过**，强化目标位置/顺序断言后重跑相关子集 **136 项通过**；
Harness **13 项通过**，TypeScript 类型检查通过。只读代码审查无阻塞意见。
所有实测租约已释放，临时侧车退出，临时启动的 Ollama 已关闭；原有服务及真实项目未改。

[本轮四次实测原始结果](../../.tmp/harness-live-20260912/slim-results-090736/summary.json)

```powershell
python scripts/evaluate_harness_live.py --cases edit24_all,edit24_short --runtimes legacy,harness --title-only --label slim-results --timeout 300
```

## 后续修复：分离推理步数与工具预算（2026-09-12）

保留 `DS_HARNESS_MAX_STEPS=12`（可配置 1–32），新增
`DS_HARNESS_MAX_TOOL_CALLS=64`（可配置 1–64）。前者约束模型步骤，后者约束
每轮进入工具校验的不同调用；参数、可用性及旧状态拒绝也消耗工具预算。
重复调用仍在准入前拒绝，刷新上下文或重新推理不会补充预算。达到上限时，
错误明确要求报告已完成与待完成项，不再重试。本轮没有修改 Node 推理循环、
防重放/状态检查、256 个能力请求的传输上限或长上下文处理。

TDD：真实 Node 集成测试在修复前复现「设置 2 个模型步骤就只能保存 2 镜」。
修复后，在 **2 个模型步骤内保存 24 镜并返回**，第 25 镜保持完全不变；
另设工具预算 1 时，只保存第一镜，后续 23 次明确拒绝。
新测试也验证错误调用消耗预算、后续正常推理不重置预算，以及越界配置被拒绝。
相关 Python 回归 **375 项通过**，Harness **13 项通过**，类型检查通过，
只读代码审查无阻塞意见。

真实 Qwen 仍采用 32768 上下文、4096 输出，且只修改标题：seed 42 的
24 镜全改 **24/24 落盘并正常回复**，耗时 33.2 秒（含一次冷启动预热），
实际业务推理仅 2 次；3 镜局部修改也通过，17.8 秒。全改的后续请求
79,211 字符、20,182 输入 token，无 HTTP 500，也没有第 12 镜后的预算拒绝。
这是有限场景验证，不代表长历史或数字字符串参数问题已经修复。

[预算分离后的 seed 42 实测结果](../../.tmp/harness-live-20260912/separate-budget-091642/summary.json)

补充 seed 7 的全 24 镜复测也通过：24/24 保存，2 次业务推理，29.1 秒，
正常回复，无额外工具重试。合计本轮 **3/3 案例通过**，但样本数不足以估计
统计失败率。[seed 7 原始结果](../../.tmp/harness-live-20260912/separate-budget-seed7-091746/summary.json)。
所有测试租约已释放，临时侧车及本轮启动的 Ollama 已停止，原有服务保留。

```powershell
python scripts/evaluate_harness_live.py --cases edit24_all,edit24_short --runtimes harness --title-only --label separate-budget --timeout 300
python scripts/evaluate_harness_live.py --cases edit24_all --runtimes harness --title-only --seed 7 --label separate-budget-seed7 --timeout 300
```

## 后续修复：首轮历史压缩（2026-09-12）

本轮仅修改 Node 接入层：在第一次 pre-step 前，将已知的 host 路由及初始项目/
工具信息写入临时 session 的 header，供原生压力估算使用；首次正式模型请求
仍由原生 loop 写入实际 header。这样不需要先发送一个超长请求来触发路由初始化。
同时保留原生摘要/历史替换实现，增加失败即停止边界：摘要报错、输出截断或未缩小
内容时，不再沿用原始超长历史继续业务推理。已完成的写入不会回滚。

TDD 新增首轮压缩顺序、历史加大项目资料后的压力检查，以及三类摘要失败停止测试；
原有上下文溢出恢复测试也明确验证 `turn → compaction → turn`，避免被提前压缩
替代后失去覆盖。最终 **18 项 Harness 测试与类型检查通过**；相关 Python
**375 项回归通过**，最后的接入层修改后再跑 **28 项 Harness/Python 集成测试通过**。
只读代码审查无阻塞意见。

最终代码实测仍用 qwen3.8:27b、seed 42、32768 上下文、4096 输出；只改标题，
不修改数值校验。每格一次，不能当作统计成功率：

| 案例 | 结果 | 实际业务/摘要输入 |
|---|---|---|
| 162 条历史，修改 3 镜 | 通过，45.2 秒；保留历史最早的【蓝灯】约定 | 先摘要 21,577 token，再业务 19,461 / 20,139 token |
| 82 条历史，修改 3 镜 | 通过，28.4 秒；保留【蓝灯】约定 | 无摘要，业务 26,773 / 27,451 token |
| 无历史，修改全部 24 镜 | 通过，28.8 秒；24/24 保存 | 2 次业务推理，最后输入 20,182 token |

初测 162 条历史首个业务请求为 32,682 token，只输出 83 token 后截断；最终复测
首个业务输入降到 **19,461 token**，摘要请求本身也正常结束。指定镜头、顺序、
邻镜、剧本和未要求修改的创作字段均通过落盘检查。

范围限制：仍是原生约 4 字符/token、80% 阈值的启发式压力检查，不是精确的
全链路输入/输出预算系统。Python 额外技能提示、当前超长单条消息、图片 token、
更大摘要输入仍可能超过窗口；这轮没有修复重复上下文、数字字符串校验或完整 usage
转发。Python 原始聊天记录不被压缩覆盖，摘要只存在于这一轮临时 session。

- [最终代码三次实测](../../.tmp/harness-live-20260912/first-envelope-final-095734/summary.json)
- [仅路由初始化的诊断运行](../../.tmp/harness-live-20260912/first-pressure-095316/summary.json)
- [加入摘要失败停止后的中间验证](../../.tmp/harness-live-20260912/first-pressure-final-095503/summary.json)

最终判定以上面的三次最终代码测试为准，不把中间版本混入统计。测试租约全部释放，
临时侧车和本轮启动的 Ollama 已关闭；原有服务及真实项目保留。

```powershell
python scripts/evaluate_harness_live.py --cases edit24_overflow,edit24_long,edit24_all --runtimes harness --title-only --label first-envelope-final --timeout 300
```

## 后续修复：单镜修改数字参数归一化（2026-09-12）

本轮仅调整 Python Harness 的 `revise_shot` 接入：先通过既有
`ShotRevisionSubmission` 业务模型归一化，再执行 JSONSchema、工具可用性和
当前状态检查。使用 `exclude_unset=True`，不会给未要求修改的字段补空值。
`"7"`、`"7.0"`、`7`、`7.0` 均保存为 7 秒；执行时记录原始与归一化指纹，
换一种数值写法也不能重放同一次修改。完全相同的调用在准入前拒绝，另一种
等价写法可能先消耗一次工具预算再被拒绝；预算不会因重新推理而重置。

模糊文字、零、负数、布尔值、空值、NaN/Infinity 和额外字段仍被拒绝。
没有增加全局类型转换器，也没有放宽其他工具的校验。此轮仍不处理重复上下文
或完整输入/输出预算。

TDD 先复现字符串参数被拒绝及等价数值未去重，再修正；测试覆盖真实落盘、
邻镜和未指定字段不变、等价调用仅执行一次、八类非法参数拒绝。
真实 Node 集成测试还覆盖参数修复后提交字符串时长并实际保存。
相关 Python 回归 **386 项通过**，Harness **18 项通过**，类型检查通过。
独立只读代码审查未发现阻塞问题。

真实 qwen3.8:27b、seed 42、32768 上下文、4096 输出；本次恢复同时修改
标题与时长，不再使用 `--title-only`。每个案例一次：

| 案例 | 结果 | 耗时 |
|---|---|---|
| 4 镜中修改第 2 镜 | 通过，1/1 保存 | 17.1 秒，含冷启动预热 |
| 24 镜中修改第 3/12/24 镜 | 通过，3/3 保存 | 18.0 秒 |
| 修改全部 24 镜 | 通过，24/24 保存 | 30.7 秒 |
| 162 条历史，修改 3 镜 | 通过，保留最早的【蓝灯】约定 | 45.6 秒 |

实际 **31 次工具调用全部使用字符串 `"7"`**，均归一化后成功执行，无参数拒绝
或重复重试。每案均为 2 次业务推理；长历史另有 1 次摘要，首个业务输入
19,464 token。所有目标标题/时长、镜头身份/顺序、剧本、邻镜及未要求修改的
创作字段均通过保存结果检查，最终正常回复。**4/4 是这些夹具的结果，不是
统计成功率，也不代表所有长上下文任务均已解决。**

所有提供方租约释放，临时侧车及本轮启动的 Ollama 已停止；原有服务与真实
项目保留。代码仍在隔离 worktree，未提交或合并。

[本轮四次实测原始结果](../../.tmp/harness-live-20260912/numeric-normalization-100705/summary.json)

```powershell
python scripts/evaluate_harness_live.py --cases edit4_short,edit24_short,edit24_all,edit24_overflow --runtimes harness --label numeric-normalization --timeout 300
```
