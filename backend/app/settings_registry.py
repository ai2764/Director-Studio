"""Metadata for every user-configurable ``DS_*`` setting.

tier:
  user      -> wizard / main settings tabs
  advanced  -> "高级 / Advanced" tab
  internal  -> never exposed or writable through the API (env only)
apply:
  hot      -> applied in-place to the live ``settings`` object (+ cache rebuild)
  comfy    -> applied in-place, then the ComfyUI runtime is reconfigured
              (managed ComfyUI restarted / proxy re-targeted) when idle
  restart  -> persisted to .env, takes effect after a DS restart
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Tier = Literal["user", "advanced", "internal"]
Apply = Literal["hot", "comfy", "restart"]
FieldType = Literal["str", "int", "float", "bool", "enum", "multi", "url", "path"]


@dataclass(frozen=True)
class FieldSpec:
    key: str
    attr: str
    group: str
    tier: Tier
    apply: Apply
    type: FieldType = "str"
    secret: bool = False
    options: tuple[str, ...] = ()
    label_zh: str = ""
    label_en: str = ""
    help_zh: str = ""
    help_en: str = ""
    aliases: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "key": self.key,
            "group": self.group,
            "tier": self.tier,
            "apply": self.apply,
            "type": self.type,
            "secret": self.secret,
            "label": {"zh": self.label_zh or self.key, "en": self.label_en or self.key},
            "help": {"zh": self.help_zh, "en": self.help_en},
        }
        if self.options:
            data["options"] = list(self.options)
        if self.minimum is not None:
            data["min"] = self.minimum
        if self.maximum is not None:
            data["max"] = self.maximum
        if self.extra:
            data.update(self.extra)
        return data


def _f(key: str, group: str, tier: Tier, apply: Apply, type_: FieldType = "str", *,
       zh: str = "", en: str = "", hzh: str = "", hen: str = "", **kw: Any) -> FieldSpec:
    attr = kw.pop("attr", key.removeprefix("DS_").lower())
    return FieldSpec(key=key, attr=attr, group=group, tier=tier, apply=apply, type=type_,
                     label_zh=zh, label_en=en, help_zh=hzh, help_en=hen, **kw)


GROUPS: dict[str, dict[str, str]] = {
    "llm": {"zh": "导演大模型", "en": "Director LLM"},
    "comfy": {"zh": "ComfyUI 连接", "en": "ComfyUI connection"},
    "comfy_launch": {"zh": "ComfyUI 启动选项", "en": "ComfyUI launch options"},
    "h3": {"zh": "H3 视频", "en": "H3 video"},
    "jobs": {"zh": "任务与队列", "en": "Jobs & queue"},
    "agent": {"zh": "导演 Agent", "en": "Director agent"},
    "system": {"zh": "系统", "en": "System"},
    "deps": {"zh": "工作流依赖下载", "en": "Workflow dependency downloads"},
}

PRECISIONS = ("auto", "fp8_e4m3fn", "fp8_e5m2", "fp16", "bf16", "fp32")

FIELDS: tuple[FieldSpec, ...] = (
    # ---- LLM -------------------------------------------------------------
    _f("DS_LLM_PROVIDER", "llm", "user", "hot", "enum",
       options=("ollama", "lm-studio", "llama-swap", "openai-compatible"),
       zh="大模型服务", en="LLM provider",
       hzh="Ollama / LM Studio / llama-swap / 任何 OpenAI 兼容接口（含远程隧道）",
       hen="Ollama / LM Studio / llama-swap / any OpenAI-compatible endpoint (tunnels too)"),
    _f("DS_LLAMA_SWAP_MODE", "llm", "user", "hot", "enum", options=("bundled", "remote"),
       zh="llama-swap 来源", en="llama-swap source",
       hzh="bundled = DS 内置 llama.cpp（llama-swap 按需启动 llama-server，模型放在 DS 模型目录 llm/，配置自动生成）；remote = 你自己的 llama-swap（填写地址，上下文长度从服务端读取）",
       hen="bundled = DS's built-in llama.cpp (llama-swap starts llama-server on demand, models in the DS models folder llm/, config generated automatically); remote = your own llama-swap at the base URL (context read from the server)"),
    _f("DS_LLAMA_MODELS_DIR", "llm", "advanced", "hot", "path", zh="内置 llama.cpp 模型目录", en="Built-in llama.cpp models folder",
       hzh="存放 .gguf 的目录；留空 = <模型目录>/llm", hen="Folder with .gguf files; empty = <models dir>/llm"),
    _f("DS_LLAMA_GPU_LAYERS", "llm", "advanced", "hot", zh="内置 llama.cpp GPU 层数", en="Built-in llama.cpp GPU layers",
       hzh="auto / all / 数字（0 = 仅 CPU）", hen="auto / all / a number (0 = CPU only)"),
    _f("DS_LLAMA_MTP", "llm", "advanced", "hot", "bool", zh="MTP 推测解码（模型支持时）",
       en="MTP speculative decoding (when the model supports it)"),
    _f("DS_LLAMA_SWAP_PORT", "llm", "advanced", "hot", "int", zh="内置 llama-swap 端口", en="Built-in llama-swap port",
       hzh="0 = 自动选择空闲端口", hen="0 = pick a free port automatically", minimum=0, maximum=65535),
    _f("DS_LLAMA_SWAP_DIR", "llm", "advanced", "hot", "path", zh="内置 llama-swap 程序目录", en="Built-in llama-swap folder",
       hzh="留空 = 安装包内 runtime/llama-swap", hen="Empty = runtime/llama-swap in the package"),
    _f("DS_LLAMA_CPP_DIR", "llm", "advanced", "hot", "path", zh="内置 llama.cpp 程序目录", en="Built-in llama.cpp folder",
       hzh="包含 llama-server 的目录；留空 = 安装包内 runtime/llama.cpp", hen="Folder containing llama-server; empty = runtime/llama.cpp in the package"),
    _f("DS_OLLAMA_MODE", "llm", "user", "hot", "enum", options=("system", "bundled"),
       zh="Ollama 来源", en="Ollama source",
       hzh="bundled = 使用 DS 内置 Ollama（独立端口、模型存放在 DS 数据目录，不影响本机 Ollama）；system = 本机已安装的 Ollama",
       hen="bundled = DS's private Ollama (own port, models in the DS data folder, coexists with yours); system = your installed Ollama"),
    _f("DS_OLLAMA_BASE_URL", "llm", "user", "hot", "url", zh="Ollama 地址", en="Ollama URL",
       hzh="仅 provider=ollama 且来源为本机 Ollama 时使用，默认 http://127.0.0.1:11434",
       hen="Used for provider=ollama with your own Ollama. Default http://127.0.0.1:11434"),
    _f("DS_OLLAMA_MODELS_DIR", "llm", "advanced", "hot", "path", zh="内置 Ollama 模型目录", en="Bundled Ollama models folder",
       hzh="留空 = <数据目录>/ollama/models", hen="Empty = <data dir>/ollama/models"),
    _f("DS_OLLAMA_DIR", "llm", "advanced", "hot", "path", zh="内置 Ollama 程序目录", en="Bundled Ollama folder",
       hzh="包含 bin/ollama 的目录；留空 = 安装包内 runtime/ollama", hen="Folder containing bin/ollama; empty = runtime/ollama in the package"),
    _f("DS_OLLAMA_PORT", "llm", "advanced", "hot", "int", zh="内置 Ollama 端口", en="Bundled Ollama port",
       hzh="0 = 自动选择空闲端口（不占用 11434）", hen="0 = pick a free port automatically (never 11434)", minimum=0, maximum=65535),
    _f("DS_LLM_BASE_URL", "llm", "user", "hot", "url", zh="OpenAI 兼容地址", en="OpenAI-compatible base URL",
       hzh="例如 http://127.0.0.1:1234/v1 或 https://<隧道>/v1；留空使用各 provider 默认值",
       hen="e.g. http://127.0.0.1:1234/v1 or https://<tunnel>/v1; empty = provider default"),
    _f("DS_LLM_API_KEY", "llm", "user", "hot", secret=True, zh="API Key", en="API key",
       hzh="云端或带鉴权的接口需要；不会回显", hen="Needed for cloud/authenticated endpoints; never shown again"),
    _f("DS_LLM_EXTRA_HEADERS", "llm", "user", "hot", secret=True, zh="额外请求头", en="Extra HTTP headers",
       hzh="用于隧道鉴权（如 Cloudflare Access）。JSON 对象，或每行一个 `Key: value`",
       hen="For tunnel auth (e.g. Cloudflare Access). JSON object or one `Key: value` per line",
       extra={"multiline": True}),
    _f("DS_LLM_TIMEOUT_SEC", "llm", "advanced", "hot", "float", zh="LLM 超时（秒）", en="LLM timeout (s)", minimum=5),
    _f("DS_DIRECTOR_NUM_CTX", "llm", "advanced", "hot", "int", zh="上下文长度（无法自动探测时）",
       en="Context length fallback", minimum=1024,
       hzh="内置 llama.cpp 用作 -c；远程 llama-swap/LM Studio/Ollama 从服务端读取",
       hen="Used as -c for the built-in llama.cpp; remote llama-swap/LM Studio/Ollama report their own"),
    _f("DS_DIRECTOR_NUM_PREDICT", "llm", "advanced", "hot", "int", zh="单次最大输出 token", en="Max output tokens",
       minimum=64),
    _f("DS_LLM_KEEP_LOADED", "llm", "advanced", "hot", "bool", zh="多轮对话间保持模型常驻",
       en="Keep local LLM loaded between turns"),
    # ---- Agent -----------------------------------------------------------
    _f("DS_DIRECTOR_AGENT_RUNTIME", "agent", "user", "restart", "enum", options=("harness", "legacy"),
       zh="导演 Agent 运行时", en="Director agent runtime",
       hzh="harness 需要模型支持原生工具调用；否则选 legacy",
       hen="harness requires native tool calling; otherwise choose legacy"),
    _f("DS_HARNESS_MAX_STEPS", "agent", "advanced", "hot", "int", zh="Harness 最大步数", en="Harness max steps",
       minimum=1, maximum=32),
    _f("DS_HARNESS_MAX_TOOL_CALLS", "agent", "advanced", "hot", "int", zh="Harness 最大工具调用",
       en="Harness max tool calls", minimum=1, maximum=64),
    _f("DS_HARNESS_TURN_TIMEOUT_SEC", "agent", "advanced", "hot", "float", zh="Harness 单轮超时（秒）",
       en="Harness turn timeout (s)", minimum=1, maximum=7200),
    # ---- ComfyUI connection ---------------------------------------------
    _f("DS_COMFY_MODE", "comfy", "user", "comfy", "enum", options=("managed", "external-local", "remote"),
       zh="ComfyUI 模式", en="ComfyUI mode",
       hzh="managed=DS 内置并在后台启动；external-local=本机已有 ComfyUI；remote=远程（隧道）",
       hen="managed = bundled, started by DS; external-local = your own local ComfyUI; remote = via tunnel"),
    _f("DS_COMFY_BASE_URL", "comfy", "user", "comfy", "url", zh="本机 ComfyUI 地址", en="Local ComfyUI URL",
       hzh="external-local 模式使用，只允许回环地址，例如 http://127.0.0.1:8188",
       hen="external-local mode; loopback only, e.g. http://127.0.0.1:8188"),
    _f("DS_COMFY_DIR", "comfy", "user", "comfy", "path", zh="内置 ComfyUI 目录", en="Bundled ComfyUI folder",
       hzh="managed 模式：包含 main.py 的目录", hen="managed mode: folder containing main.py"),
    _f("DS_COMFY_MODELS_DIR", "comfy", "user", "comfy", "path", zh="模型目录", en="Models folder",
       hzh="DS 指定的模型目录（通过 extra_model_paths.yaml 挂载），留空 = data/models",
       hen="DS models folder mounted via extra_model_paths.yaml; empty = data/models"),
    _f("DS_COMFY_PYTHON", "comfy", "advanced", "comfy", "path", zh="ComfyUI Python", en="ComfyUI Python",
       hzh="留空自动查找 venv / python_embeded", hen="Empty = auto-detect venv / python_embeded"),
    _f("DS_COMFY_PORT", "comfy", "advanced", "comfy", "int", zh="内置 ComfyUI 端口（0=自动）",
       en="Managed ComfyUI port (0 = auto)", minimum=0, maximum=65535),
    _f("DS_COMFY_STARTUP_TIMEOUT_SEC", "comfy", "advanced", "comfy", "float", zh="启动等待（秒）",
       en="Startup timeout (s)", minimum=5),
    _f("DS_COMFY_REMOTE_URL", "comfy", "user", "comfy", "url", zh="远程 ComfyUI 地址", en="Remote ComfyUI URL",
       hzh="http(s)://主机[:端口][/路径前缀]，经 DS 本地代理访问",
       hen="http(s)://host[:port][/prefix], accessed through DS's loopback proxy"),
    _f("DS_COMFY_REMOTE_AUTH_TYPE", "comfy", "user", "comfy", "enum", options=("none", "bearer", "basic", "headers"),
       zh="远程鉴权方式", en="Remote auth type"),
    _f("DS_COMFY_REMOTE_AUTH", "comfy", "user", "comfy", secret=True, zh="远程鉴权凭据", en="Remote credentials",
       hzh="bearer: 令牌；basic: 用户名:密码；headers: JSON 或每行 `Key: value`（如 CF-Access-Client-Id）",
       hen="bearer: token; basic: user:password; headers: JSON or `Key: value` lines (e.g. CF-Access-Client-Id)",
       extra={"multiline": True}),
    _f("DS_COMFY_REMOTE_VERIFY_TLS", "comfy", "user", "comfy", "bool", zh="校验 TLS 证书", en="Verify TLS"),
    _f("DS_COMFY_PROXY_PORT", "comfy", "advanced", "comfy", "int", zh="本地代理端口（0=自动）",
       en="Local proxy port (0 = auto)", minimum=0, maximum=65535),
    _f("DS_QWEN_IMAGE_21_COMFY_BASE_URL", "comfy", "advanced", "hot", "url", zh="布局生成专用 ComfyUI",
       en="Layout ComfyUI override", hzh="留空 = 与主 ComfyUI 相同", hen="Empty = same as main ComfyUI"),
    _f("DS_COMFY_MCP_COMMAND", "comfy", "advanced", "comfy", "path", zh="comfy-mcp 命令", en="comfy-mcp command"),
    _f("DS_COMFY_MCP_ARGS", "comfy", "advanced", "comfy", zh="comfy-mcp 参数", en="comfy-mcp args"),
    _f("DS_COMFY_MCP_COMFY_BIN", "comfy", "advanced", "comfy", "path", zh="comfy 命令", en="comfy CLI command"),
    # ---- ComfyUI launch options (managed mode) -------------------------
    _f("DS_COMFY_VRAM_PRESET", "comfy_launch", "user", "comfy", "enum", options=("auto", "high", "save", "extreme"),
       zh="显存模式", en="VRAM preset",
       hzh="auto=默认；high=大显存常驻；save=省显存（关闭智能内存+预留）；extreme=极限省显存",
       hen="auto=default; high=keep models in VRAM; save=disable smart memory + reserve; extreme=--novram"),
    _f("DS_COMFY_RESERVE_VRAM_GB", "comfy_launch", "user", "comfy", "float", zh="预留显存（GB，0=默认）",
       en="Reserve VRAM (GB, 0 = default)", minimum=0, maximum=256),
    _f("DS_COMFY_CUDA_DEVICE", "comfy_launch", "user", "comfy", zh="使用的显卡编号", en="GPU index",
       hzh="留空=默认；例如 0 或 0,1", hen="Empty = default; e.g. 0 or 0,1"),
    _f("DS_COMFY_RAM_PRESET", "comfy_launch", "user", "comfy", "enum", options=("auto", "low"),
       zh="内存模式", en="RAM preset", hzh="low=不缓存、禁用锁页内存（内存 ≤16GB 时）",
       hen="low = --cache-none --disable-pinned-memory (≤16 GB RAM)"),
    _f("DS_COMFY_PRECISION_PRESET", "comfy_launch", "user", "comfy", "enum", options=("auto", "fp8"),
       zh="精度模式", en="Precision preset", hzh="fp8=UNet 与文本编码器以 fp8 存储，省显存",
       hen="fp8 = store UNet and text encoder weights in fp8"),
    _f("DS_COMFY_ATTENTION", "comfy_launch", "advanced", "comfy", "enum",
       options=("auto", "sage", "pytorch", "split", "quad"), zh="注意力实现", en="Attention backend",
       hzh="sage 仅在已安装 sageattention 时生效", hen="sage only when sageattention is importable"),
    _f("DS_COMFY_UNET_PRECISION", "comfy_launch", "advanced", "comfy", "enum", options=PRECISIONS,
       zh="UNet 精度", en="UNet precision"),
    _f("DS_COMFY_TEXT_ENC_PRECISION", "comfy_launch", "advanced", "comfy", "enum", options=PRECISIONS,
       zh="文本编码器精度", en="Text encoder precision"),
    _f("DS_COMFY_VAE_PRECISION", "comfy_launch", "advanced", "comfy", "enum", options=("auto", "fp16", "bf16", "fp32"),
       zh="VAE 精度", en="VAE precision"),
    _f("DS_COMFY_FAST", "comfy_launch", "advanced", "comfy", "multi",
       options=("fp16_accumulation", "fp8_matrix_mult", "cublas_ops", "autotune"),
       zh="--fast 优化项", en="--fast optimizations", hzh="实验性，可能影响画质", hen="Experimental"),
    _f("DS_COMFY_PREVIEW_METHOD", "comfy_launch", "advanced", "comfy", "enum",
       options=("default", "none", "auto", "latent2rgb", "taesd"), zh="预览方式", en="Preview method"),
    _f("DS_COMFY_TROUBLESHOOT", "comfy_launch", "advanced", "comfy", "multi",
       options=("disable_all_custom_nodes", "disable_cuda_malloc", "disable_dynamic_vram", "disable_xformers",
                "disable_async_offload", "disable_mmap", "deterministic"),
       zh="故障排查开关", en="Troubleshooting toggles"),
    _f("DS_COMFY_HF_ENDPOINT", "comfy_launch", "advanced", "comfy", "url", zh="HF 镜像（HF_ENDPOINT）",
       en="HF_ENDPOINT mirror", hzh="例如 https://hf-mirror.com", hen="e.g. https://hf-mirror.com"),
    # ---- workflow dependency downloader ------------------------------------
    _f("DS_DEPS_HF_ENDPOINT", "deps", "user", "hot", "url", zh="Hugging Face 镜像", en="Hugging Face mirror",
       hzh="下载模型时使用，例如 https://hf-mirror.com；留空 = 使用 ComfyUI 的 HF 镜像设置或 huggingface.co",
       hen="Used for model downloads, e.g. https://hf-mirror.com; empty = ComfyUI's HF mirror setting or huggingface.co"),
    _f("DS_HF_TOKEN", "deps", "user", "hot", secret=True, zh="Hugging Face Token", en="Hugging Face token",
       hzh="仅下载需同意条款（gated）的模型时需要；只发送给 Hugging Face，不会回显",
       hen="Only needed for gated models; sent only to Hugging Face, never shown again"),
    _f("DS_DEPS_CONCURRENCY", "deps", "advanced", "hot", "int", zh="同时下载数", en="Parallel downloads",
       minimum=1, maximum=4),
    _f("DS_COMFY_HTTP_PROXY", "comfy_launch", "advanced", "comfy", "url", zh="HTTP 代理", en="HTTP(S) proxy",
       hzh="设置 ComfyUI 进程的 HTTP_PROXY/HTTPS_PROXY", hen="Sets HTTP_PROXY/HTTPS_PROXY for ComfyUI"),
    _f("DS_COMFY_EXTRA_ARGS", "comfy_launch", "advanced", "comfy", zh="额外启动参数", en="Extra arguments",
       hzh="会按 ComfyUI --help 校验；网络/端口/设备类参数被禁止",
       hen="Validated against ComfyUI --help; network/port/device flags are blocked"),
    # ---- H3 ----------------------------------------------------------------
    _f("DS_H3_PROVIDER", "h3", "user", "hot", "enum", options=("local", "minimax"), zh="H3 视频生成方式",
       en="H3 video provider", hzh="local=本地 ComfyUI；minimax=MiniMax 云（按量计费）",
       hen="local = ComfyUI; minimax = MiniMax cloud (paid)"),
    _f("DS_H3_MINIMAX_API_KEY", "h3", "user", "hot", secret=True, zh="MiniMax API Key", en="MiniMax API key",
       aliases=("MINIMAX_API_KEY",)),
    _f("DS_H3_MINIMAX_BASE_URL", "h3", "user", "hot", "url", zh="MiniMax 接口地址", en="MiniMax base URL",
       hzh="国际站 https://api.minimax.io ；国内站 https://api.minimaxi.com",
       hen="Global https://api.minimax.io ; China https://api.minimaxi.com"),
    _f("DS_H3_MINIMAX_RESOLUTION", "h3", "user", "hot", "enum", options=("768P", "2K"), zh="MiniMax 分辨率",
       en="MiniMax resolution"),
    _f("DS_H3_BUILTIN_WORKFLOW", "h3", "advanced", "hot", "enum",
       options=("h3_ref2va_turbo4_video_context.api.json", "h3_ref2va_fast4.api.json", "h3_ref2va.api.json"), zh="内置 H3 工作流", en="Built-in H3 workflow"),
    _f("DS_H3_MINIMAX_POLL_INTERVAL_SEC", "h3", "advanced", "hot", "float", zh="MiniMax 轮询间隔", en="MiniMax poll (s)"),
    _f("DS_H3_MINIMAX_TIMEOUT_SEC", "h3", "advanced", "hot", "float", zh="MiniMax 超时", en="MiniMax timeout (s)"),
    _f("DS_H3_MINIMAX_MAX_GET_RETRIES", "h3", "advanced", "hot", "int", zh="MiniMax 查询重试", en="MiniMax retries"),
    _f("DS_H3_MINIMAX_RETRY_DELAY_SEC", "h3", "advanced", "hot", "float", zh="MiniMax 重试间隔", en="MiniMax retry delay"),
    _f("DS_H3_MINIMAX_MAX_REQUEST_MB", "h3", "advanced", "hot", "float", zh="MiniMax 请求上限 MB",
       en="MiniMax max request MB"),
    # ---- jobs ------------------------------------------------------------
    _f("DS_POLL_INTERVAL_SEC", "jobs", "advanced", "hot", "float", zh="任务轮询间隔（秒）", en="Job poll interval (s)"),
    _f("DS_JOB_TIMEOUT_SEC", "jobs", "advanced", "hot", "float", zh="任务超时（秒）", en="Job timeout (s)"),
    _f("DS_VRAM_SHARED_GPU", "jobs", "user", "hot", "enum", options=("auto", "true", "false"),
       zh="大模型和 ComfyUI 共用显卡", en="LLM and ComfyUI share a GPU",
       hzh="true = 同一块显卡：生成前卸载大模型、对话前释放 ComfyUI 显存（远程 ComfyUI 也生效）；false = 不同显卡，不做交接；"
           "auto = 本机 ComfyUI 视为共用，远程时按地址判断（两端都是 127.0.0.1 隧道/转发时视为共用）",
       hen="true = same card: unload the LLM before generation and free ComfyUI before LLM turns (remote ComfyUI too); "
           "false = separate GPUs, no handoff; auto = local ComfyUI is shared, remote is decided by host "
           "(two loopback tunnels/forwards count as shared)"),
    _f("DS_VRAM_ACQUIRE_TIMEOUT_SEC", "jobs", "advanced", "hot", "float", zh="GPU 排队超时（秒）",
       en="GPU queue timeout (s)"),
    # ---- system ----------------------------------------------------------
    _f("DS_FFMPEG_DIR", "system", "advanced", "hot", "path", zh="FFmpeg 目录", en="FFmpeg folder",
       hzh="包含 ffmpeg/ffprobe 的目录；留空 = 优先使用安装包内置 FFmpeg，其次 PATH",
       hen="Folder with ffmpeg/ffprobe; empty = bundled FFmpeg first, then PATH"),
    _f("DS_HOST", "system", "advanced", "restart", zh="监听地址", en="Listen host",
       hzh="改为 0.0.0.0 会暴露到局域网，请谨慎", hen="0.0.0.0 exposes DS to your LAN"),
    _f("DS_PORT", "system", "advanced", "restart", "int", zh="端口", en="Port", minimum=1, maximum=65535),
    # ---- internal (env only; never exposed) -------------------------------
    _f("DS_COMFY_DEV_CPU", "comfy_launch", "internal", "comfy", "bool"),
    _f("DS_HARNESS_INTERNAL_TOKEN", "agent", "internal", "restart", secret=True),
    _f("DS_HARNESS_BASE_URL", "agent", "internal", "restart"),
    _f("DS_HARNESS_MANAGED", "agent", "internal", "restart", "bool"),
)

BY_KEY: dict[str, FieldSpec] = {spec.key: spec for spec in FIELDS}
PUBLIC_FIELDS: tuple[FieldSpec, ...] = tuple(spec for spec in FIELDS if spec.tier != "internal")
SECRET_KEYS: frozenset[str] = frozenset(spec.key for spec in FIELDS if spec.secret)


def schema() -> dict[str, Any]:
    return {
        "groups": GROUPS,
        "fields": [spec.public() for spec in PUBLIC_FIELDS],
    }
