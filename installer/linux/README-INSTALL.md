# Director Studio for Linux / Linux 版

## 中文

1. 解压到任意目录（例如 `~/Apps/DirectorStudio`），运行 `./director-studio`，浏览器会打开 http://127.0.0.1:8790 并进入配置向导。
2. 可选：`./install-desktop-entry.sh` 添加到应用菜单。
3. 配置文件和数据（`.env`、项目、ComfyUI 输出、Ollama 模型、ComfyUI 模型 `data/models/`）保存在 `~/.local/share/DirectorStudio`（可用环境变量 `DS_HOME` 修改），升级时只需替换程序目录。
4. 停止：在终端按 Ctrl+C，或运行 `./director-studio stop`。
5. ComfyUI 模型需要自行下载并放入 `~/.local/share/DirectorStudio/data/models/<类别>/`，清单见 `docs/models.md`；导演大模型在 设置 → 导演大模型 → 模型管理 中通过 Ollama 下载。
6. 第三方组件许可见 `THIRD_PARTY_NOTICES.txt`，GPL/LGPL 组件源码见 `SOURCE_OFFER.txt`。

## English

1. Extract anywhere and run `./director-studio`; the browser opens http://127.0.0.1:8790 with the setup wizard.
2. Optional: `./install-desktop-entry.sh` adds a menu entry.
3. Config and data live in `~/.local/share/DirectorStudio` (override with `DS_HOME`) and survive upgrades — replace only the program folder.
4. Stop with Ctrl+C or `./director-studio stop`.
5. Put ComfyUI models in `~/.local/share/DirectorStudio/data/models/<folder>/` (see `docs/models.md`); pull LLMs in Settings → Director LLM → Models.
6. Licences: `THIRD_PARTY_NOTICES.txt`; GPL/LGPL sources: `SOURCE_OFFER.txt`.
