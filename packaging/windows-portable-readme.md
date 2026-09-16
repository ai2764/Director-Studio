# Director Studio — Windows Portable Instructions

## 1. Extract the package

Extract the complete ZIP to a writable folder, for example
`C:\DirectorStudio`. Keep all included files together; do not copy only
`DirectorStudio.exe`.

Director Studio creates a `data` folder beside the executable. Keep this folder
when moving or upgrading the application because it contains projects, generated
files, logs, and the managed tool environment.

## 2. Start the external services

Before starting Director Studio, run:

- one LLM server: Ollama, LM Studio, llama.cpp, or another OpenAI-compatible
  service;
- ComfyUI at `http://127.0.0.1:8188` when using local image or video generation.

Director Studio includes its own Harness, Node.js, and Python runtimes. Do not
install Node.js, Python, `comfy-mcp`, or `comfy-cli` for the portable package.

## 3. Configure `.env`

Open the `.env` file beside `DirectorStudio.exe` and choose one LLM provider.
The model itself is selected later from the Director page.

Ollama, the default:

```dotenv
DS_COMFY_BASE_URL=http://127.0.0.1:8188
DS_LLM_PROVIDER=ollama
DS_OLLAMA_BASE_URL=http://127.0.0.1:11434
```

LM Studio:

```dotenv
DS_COMFY_BASE_URL=http://127.0.0.1:8188
DS_LLM_PROVIDER=lm-studio
DS_LLM_BASE_URL=http://127.0.0.1:1234/v1
```

llama.cpp or another OpenAI-compatible service:

```dotenv
DS_COMFY_BASE_URL=http://127.0.0.1:8188
DS_LLM_PROVIDER=openai-compatible
DS_LLM_BASE_URL=http://127.0.0.1:8080/v1
# DS_LLM_API_KEY=only-if-your-service-requires-one
```

Keep `.env` private. It may contain API keys.

## 4. Start Director Studio

Double-click `DirectorStudio.exe`. The interface opens at
`http://127.0.0.1:8790`.

The first launch needs internet access. Director Studio downloads and verifies
the locked Comfy tools into `data\tools\comfy`; later launches reuse that private
copy.

## 5. Update or move the package

Back up `data` and `.env`. Extract the new package into a writable folder, then
restore those two items beside the new `DirectorStudio.exe`.

## Troubleshooting

- If first-launch setup fails, confirm that PyPI is reachable and restart the
  application.
- Check `data\logs\comfy-bootstrap.log` for tool-installation errors.
- Confirm that the selected LLM server and ComfyUI are running on the URLs in
  `.env`.
- Keep unauthenticated LLM and ComfyUI services bound to `127.0.0.1`.
