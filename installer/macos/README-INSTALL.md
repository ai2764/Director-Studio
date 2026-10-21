# Director Studio for macOS / macOS 版

## 中文

1. 解压到任意目录（例如 `~/Applications/DirectorStudio`），双击 `Director Studio.command`（或在终端运行 `./director-studio`），浏览器会打开 http://127.0.0.1:8790 并进入配置向导。
2. 此版本未签名/未公证：若 macOS 阻止运行，请右键 → 打开，或执行 `xattr -dr com.apple.quarantine <目录>`。
3. 配置和数据保存在 `~/Library/Application Support/DirectorStudio`（可用 `DS_HOME` 修改），升级时只需替换程序目录。
4. 需要 FFmpeg：`brew install ffmpeg`（或设置 `DS_FFMPEG_DIR`）。
5. Apple Silicon（arm64）版内置 ComfyUI（PyTorch MPS）；Intel（x86_64）版不含 ComfyUI，请在设置中连接外部或远程 ComfyUI。
6. 停止：Ctrl+C 或 `./director-studio stop`。许可见 `THIRD_PARTY_NOTICES.txt` / `SOURCE_OFFER.txt`。

## English

1. Extract anywhere and double-click `Director Studio.command` (or run `./director-studio`); the browser opens http://127.0.0.1:8790 with the setup wizard.
2. This build is not signed or notarized: if macOS blocks it, right-click → Open, or run `xattr -dr com.apple.quarantine <folder>`.
3. Config and data live in `~/Library/Application Support/DirectorStudio` (override with `DS_HOME`) and survive upgrades.
4. FFmpeg is required: `brew install ffmpeg` (or set `DS_FFMPEG_DIR`).
5. Apple Silicon (arm64) bundles ComfyUI with PyTorch MPS; the Intel (x86_64) package has no local ComfyUI — connect an external or remote ComfyUI in Settings.
6. Stop with Ctrl+C or `./director-studio stop`. Licences: `THIRD_PARTY_NOTICES.txt`, `SOURCE_OFFER.txt`.
