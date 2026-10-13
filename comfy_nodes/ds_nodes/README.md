# ds_nodes — Director Studio ComfyUI nodes (Apache-2.0)

Install: copy (or symlink) this folder into `ComfyUI/custom_nodes/ds_nodes`.
The DS-managed ComfyUI gets it automatically (see `scripts/dev/setup_comfy_runtime.sh`).

| Node id | Purpose |
|---|---|
| `DS Prompt List` | One prompt per non-empty line (`prepend + line + append`), sliced by `start_index` / `max_rows`; list outputs `prompt`, `body_text`, plus `show_help`. |
| `CR Prompt List` | Same implementation registered under the id used by DS's built-in scene workflow, so the workflow runs without Comfyroll. |

Written from scratch for Director Studio from the node's public interface (input
names/types and list-output behaviour as used in DS workflows); no Comfyroll code
was used. Comfyroll has no license file and therefore cannot be redistributed.
