import { useCallback, useEffect, useState } from "react";
import type { Bilingual, Lang } from "./types";

const STRINGS = {
  settings: { zh: "设置", en: "Settings" },
  close: { zh: "关闭设置", en: "Close settings" },
  tabOverview: { zh: "概览/体检", en: "Overview" },
  tabLlm: { zh: "大模型", en: "LLM" },
  tabGeneration: { zh: "生成/ComfyUI", en: "Generation" },
  tabWorkflows: { zh: "工作流", en: "Workflows" },
  tabDeps: { zh: "工作流依赖", en: "Workflow dependencies" },
  openDeps: { zh: "缺少模型/节点？去下载", en: "Missing models/nodes? Download them" },
  tabAdvanced: { zh: "高级", en: "Advanced" },
  tabSystem: { zh: "系统", en: "System" },
  save: { zh: "保存并应用", en: "Save & apply" },
  saving: { zh: "保存中…", en: "Saving…" },
  discard: { zh: "放弃修改", en: "Discard" },
  saved: { zh: "已保存", en: "Saved" },
  unsaved: { zh: "项未保存的修改", en: "unsaved change(s)" },
  restartNotice: {
    zh: "以下设置需要重启 Director Studio 才会生效：",
    en: "Restart Director Studio to apply:",
  },
  restartHint: {
    zh: "请关闭并重新运行 director-studio（或 start-all.sh）。",
    en: "Stop and re-run director-studio (or start-all.sh).",
  },
  restartApp: { zh: "重启 Director Studio", en: "Restart Director Studio" },
  installRoot: { zh: "程序目录", en: "Install folder" },
  servingAt: { zh: "当前访问地址", en: "Serving at" },
  portOverridden: {
    zh: "当前由启动器使用端口 {port}（命令行参数优先于此设置）",
    en: "The launcher currently serves on port {port} (command-line option overrides this setting)",
  },
  restartNow: { zh: "立即重启", en: "Restart now" },
  restarting: { zh: "正在重启 Director Studio…（约 10–60 秒）", en: "Restarting Director Studio… (10–60 s)" },
  restartFailed: { zh: "重启失败", en: "Restart failed" },
  restartBusy: { zh: "有生成任务正在运行，结束后再重启", en: "A generation job is running; restart when it finishes" },
  llmBundled: { zh: "内置 Ollama", en: "Bundled Ollama" },
  llmBundledDesc: {
    zh: "DS 自带，独立端口运行，模型存放在 DS 数据目录，不影响你电脑上已有的 Ollama",
    en: "Ships with DS, runs on its own port with models in the DS data folder; your own Ollama is untouched",
  },
  llmSystem: { zh: "外部 Ollama", en: "External Ollama" },
  llmSystemDesc: { zh: "使用你自己安装并运行的 Ollama（默认 127.0.0.1:11434）", en: "Your own running Ollama (default 127.0.0.1:11434)" },
  llmLmStudio: { zh: "LM Studio", en: "LM Studio" },
  llmLmStudioDesc: { zh: "本机 LM Studio 的 OpenAI 兼容服务", en: "LM Studio's local OpenAI-compatible server" },
  llmLlamaBuiltin: { zh: "内置 llama.cpp（llama-swap）", en: "Built-in llama.cpp (llama-swap)" },
  llmLlamaBuiltinDesc: {
    zh: "推荐。DS 自动启动/停止一个 llama-swap，按需加载 llama-server（支持 MTP 加速），配置自动生成",
    en: "Recommended. DS starts/stops one llama-swap that loads llama-server on demand (MTP speed-up), config generated for you",
  },
  llmLlamaBuiltinModels: {
    zh: "模型：在 设置 → 依赖下载 → “导演大模型” 中下载推荐的 GGUF，或把 .gguf 放进模型目录的 llm/ 文件夹；保存后自动生效。",
    en: "Models: download a recommended GGUF under Settings → Dependencies → \"Director LLM\", or drop a .gguf into the llm/ folder of the models directory; it is picked up automatically.",
  },
  llmLlamaSwap: { zh: "我的 llama-swap", en: "My llama-swap" },
  llmLlamaSwapDesc: { zh: "你自己运行的 llama-swap（本机或远程地址），上下文长度从服务端读取", en: "Your own llama-swap (local or remote URL); context size read from the server" },
  llmOpenAI: { zh: "OpenAI 兼容（含远程）", en: "OpenAI-compatible (incl. remote)" },
  llmOpenAIDesc: { zh: "云端 API 或远程隧道（可带鉴权请求头）", en: "Cloud APIs or remote tunnels (auth headers supported)" },
  ollamaModels: { zh: "模型管理", en: "Models" },
  ollamaPull: { zh: "下载", en: "Pull" },
  ollamaPullPlaceholder: { zh: "模型名，例如 qwen3:8b", en: "Model name, e.g. qwen3:8b" },
  ollamaDelete: { zh: "删除", en: "Delete" },
  ollamaDeleteConfirm: { zh: "确定删除模型？", en: "Delete model?" },
  ollamaLoaded: { zh: "已加载", en: "loaded" },
  ollamaUnreachable: { zh: "Ollama 未运行或无法连接", en: "Ollama is not running or unreachable" },
  ollamaSuggested: { zh: "推荐：", en: "Suggested:" },
  ollamaSaveFirst: { zh: "已修改大模型来源，请先保存，再管理模型", en: "Save the new LLM source first, then manage models" },
  ollamaNone: { zh: "还没有模型，请下载一个（导演需要支持工具调用的模型）", en: "No models yet; pull one (the director needs a tool-calling model)" },
  ollamaRegistryNote: {
    zh: "模型通过 Ollama 官方仓库下载（ollama.com/library），进度实时显示；下载可随时取消。",
    en: "Models are downloaded from the Ollama registry (ollama.com/library) with live progress; cancel any time.",
  },
  cancel: { zh: "取消", en: "Cancel" },
  useModel: { zh: "设为导演模型", en: "Use for director" },
  capTitle: { zh: "模型能力检测", en: "Model capabilities" },
  capRun: { zh: "检测工具调用 / 图像理解", en: "Test tool calling / vision" },
  capModel: { zh: "模型", en: "Model" },
  capTools: { zh: "工具调用", en: "Tool calling" },
  capVision: { zh: "图像理解", en: "Vision" },
  capYes: { zh: "支持", en: "Yes" },
  capNo: { zh: "不支持", en: "No" },
  capUnknown: { zh: "未知", en: "Unknown" },
  capPickModel: { zh: "先点“测试连接”获取模型列表，或输入模型名", en: "Run the connection test to list models, or type a model name" },
  capHarnessWarn: {
    zh: "Harness 模式需要工具调用（tool calling）。该模型未通过检测，导演将无法执行操作——请换用支持 tools 的模型（如 qwen3），或在“导演 Agent”中切换为 legacy 模式。",
    en: "Harness mode needs tool calling. This model failed the probe, so the director cannot act — pick a tools-capable model (e.g. qwen3) or switch the agent runtime to legacy.",
  },
  capHarnessOk: { zh: "✓ 可用于 Harness 模式", en: "✓ Works with Harness mode" },
  capVisionWarn: {
    zh: "该模型不能看图：审阅参考图/分镜图的功能不可用（可选功能）。",
    en: "No image input: reviewing reference frames / storyboards is unavailable (optional).",
  },
  comfyDeferred: { zh: "ComfyUI 正忙，新设置将在当前任务结束后生效", en: "ComfyUI busy; applies after current jobs" },
  lockedEnv: { zh: "由环境变量/启动脚本设置", en: "Set by environment / launcher" },
  hot: { zh: "立即生效", en: "Live" },
  comfyApply: { zh: "重启 ComfyUI 后生效", en: "Restarts ComfyUI" },
  restart: { zh: "需重启", en: "Restart" },
  secretSaved: { zh: "已保存", en: "Saved" },
  secretNotSet: { zh: "未设置", en: "Not set" },
  secretReplace: { zh: "替换", en: "Replace" },
  secretClear: { zh: "清除", en: "Clear" },
  secretKeep: { zh: "保留原值", en: "Keep" },
  secretWillClear: { zh: "保存后将清除", en: "Will be cleared" },
  test: { zh: "测试连接", en: "Test" },
  testing: { zh: "测试中…", en: "Testing…" },
  testOk: { zh: "连接成功", en: "Connected" },
  testFail: { zh: "失败", en: "Failed" },
  models: { zh: "可用模型", en: "Models" },
  directorModel: { zh: "导演模型", en: "Director model" },
  runDoctor: { zh: "重新检查", en: "Re-check" },
  doctorRunning: { zh: "体检中…", en: "Checking…" },
  doctor: { zh: "体检", en: "Health check" },
  missingNodes: { zh: "缺失节点", en: "Missing nodes" },
  noLicense: { zh: "无许可证·未内置", en: "no licence · not bundled" },
  userNodesDir: { zh: "用户节点目录", en: "User node folder" },
  missingModels: { zh: "缺失模型", en: "Missing models" },
  putModelAt: { zh: "放到", en: "Place at" },
  optional: { zh: "可选", en: "optional" },
  rerunWizard: { zh: "重新运行配置向导", en: "Run setup wizard" },
  legacyBanner: {
    zh: "检测到已有的 .env 配置。建议运行一次配置向导/体检，确认模型与节点是否齐全。",
    en: "Existing .env detected. Run the setup wizard / health check to verify models and nodes.",
  },
  openWizard: { zh: "打开向导", en: "Open wizard" },
  dismiss: { zh: "稍后", en: "Later" },
  comfyRuntime: { zh: "ComfyUI 运行状态", en: "ComfyUI runtime" },
  restartComfy: { zh: "重启 ComfyUI", en: "Restart ComfyUI" },
  showLog: { zh: "查看日志", en: "Show log" },
  hideLog: { zh: "隐藏日志", en: "Hide log" },
  launchPreview: { zh: "启动命令预览", en: "Launch command preview" },
  language: { zh: "界面语言", en: "Language" },
  wizardTitle: { zh: "Director Studio 配置向导", en: "Director Studio setup" },
  stepWelcome: { zh: "欢迎", en: "Welcome" },
  stepLlm: { zh: "导演大模型", en: "Director LLM" },
  stepGen: { zh: "生成后端", en: "Generation" },
  stepDoctor: { zh: "体检", en: "Health check" },
  stepConfirm: { zh: "确认应用", en: "Confirm" },
  next: { zh: "下一步", en: "Next" },
  back: { zh: "上一步", en: "Back" },
  skip: { zh: "跳过向导", en: "Skip" },
  finish: { zh: "完成", en: "Finish" },
  welcomeIntro: {
    zh: "接下来几步会帮你连接导演大模型（Ollama 等）和图像/视频生成后端（ComfyUI 或 MiniMax），并检查模型与节点是否齐全。所有设置都保存在 .env 中，之后可在“设置”里修改。",
    en: "A few steps to connect the Director LLM and the generation backend (ComfyUI or MiniMax) and check models/nodes. Settings are stored in .env and can be changed later.",
  },
  quickCheck: { zh: "快速自检", en: "Quick check" },
  comfyModeManaged: { zh: "内置 ComfyUI", en: "Bundled ComfyUI" },
  comfyModeManagedDesc: { zh: "DS 在后台启动并管理，推荐", en: "Started and managed by DS (recommended)" },
  comfyModeLocal: { zh: "本机已有 ComfyUI", en: "Existing local ComfyUI" },
  comfyModeLocalDesc: { zh: "连接你自己运行的 ComfyUI（127.0.0.1）", en: "Connect to your own ComfyUI on 127.0.0.1" },
  comfyModeRemote: { zh: "远程 ComfyUI", en: "Remote ComfyUI" },
  comfyModeRemoteDesc: { zh: "通过隧道访问另一台电脑（支持鉴权头）", en: "Another PC via tunnel (auth headers supported)" },
  sharedGpu: { zh: "大模型和 ComfyUI 在同一块显卡上", en: "The LLM and ComfyUI run on the same GPU" },
  sharedGpuAuto: { zh: "自动判断", en: "auto" },
  sharedGpuHelp: {
    zh: "勾选后，生成前先卸载大模型、对话前释放 ComfyUI 显存，避免两者同时占满显存导致崩溃（例如 llama-swap 与 ComfyUI 在同一台电脑）。",
    en: "When checked, DS unloads the LLM before generation and frees ComfyUI before LLM turns so both never fill VRAM at once (e.g. llama-swap and ComfyUI on one PC).",
  },
  h3Local: { zh: "H3 视频：本地 ComfyUI", en: "H3 video: ComfyUI" },
  h3Minimax: { zh: "H3 视频：MiniMax 云", en: "H3 video: MiniMax cloud" },
  h3MinimaxNote: {
    zh: "提示：角色、道具、场景、布局等图片流程仍需要 ComfyUI。",
    en: "Note: character/prop/scene/layout images still need ComfyUI.",
  },
  launchOptions: { zh: "ComfyUI 启动选项", en: "ComfyUI launch options" },
  applyAndCheck: { zh: "应用当前配置并体检", en: "Apply and run check" },
  doctorNote: {
    zh: "体检基于已应用的配置。缺失模型请手动放入下方提示的目录，然后点“重新检查”。可以跳过并保留警告。",
    en: "The check uses the applied settings. Put missing models in the folders shown, then re-check. You may continue with warnings.",
  },
  changes: { zh: "待应用的修改", en: "Pending changes" },
  noChanges: { zh: "没有待应用的修改", en: "No pending changes" },
  appliedList: { zh: "已应用", en: "Applied" },
  costNote: { zh: "云端测试只会列出模型，不产生生成费用", en: "Cloud tests only list models; no generation cost" },
  minimaxTest: { zh: "校验 Key（免费查询接口）", en: "Validate key (free query call)" },
  mobileReadonly: {
    zh: "配置只能在运行 Director Studio 的电脑上修改。",
    en: "Settings can only be changed on the computer running Director Studio.",
  },
  advancedFor: { zh: "高级设置", en: "Advanced settings" },
  overviewStatus: { zh: "当前状态", en: "Status" },
  envFile: { zh: "配置文件", en: "Config file" },
  dataDir: { zh: "数据目录", en: "Data folder" },
  logsDir: { zh: "日志目录", en: "Logs folder" },
  version: { zh: "版本", en: "Version" },
  modelsDir: { zh: "模型目录", en: "Models folder" },
  source: { zh: "来源", en: "Source" },
} satisfies Record<string, Bilingual>;

export type StringKey = keyof typeof STRINGS;

const STORAGE_KEY = "ds.lang";
const listeners = new Set<(lang: Lang) => void>();

export function currentLang(): Lang {
  try {
    // English is the default UI language; Chinese only when the user picked it.
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return stored === "zh" ? "zh" : "en";
  } catch {
    return "en";
  }
}

export function setLang(lang: Lang): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, lang);
  } catch {
    /* ignore */
  }
  listeners.forEach((fn) => fn(lang));
}

export function pick(text: Bilingual | undefined, lang: Lang): string {
  if (!text) return "";
  // Fall back to English, never to Chinese, when a translation is missing.
  return text[lang] || text.en || text.zh || "";
}

export function useI18n() {
  const [lang, setState] = useState<Lang>(currentLang);
  useEffect(() => {
    listeners.add(setState);
    return () => {
      listeners.delete(setState);
    };
  }, []);
  const t = useCallback((key: StringKey) => STRINGS[key][lang], [lang]);
  return { lang, t, setLang };
}
