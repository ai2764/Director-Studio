# SPDX-License-Identifier: Apache-2.0
# Copyright Director Studio contributors.
"""Director Studio ComfyUI nodes.

``DS Prompt List`` is an original, from-scratch implementation that is
interface-compatible with the ``CR Prompt List`` node used by DS's built-in
scene workflow (same input names/types, same list output). It is additionally
registered under the ``CR Prompt List`` node id so existing workflows run
without Comfyroll (which ships without a license and is therefore not bundled).
If Comfyroll is also installed, whichever pack loads last provides that id;
both produce one prompt per selected line.
"""

from .prompt_list import DSPromptList

NODE_CLASS_MAPPINGS = {
    "DS Prompt List": DSPromptList,
    "CR Prompt List": DSPromptList,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "DS Prompt List": "Prompt List (Director Studio)",
    "CR Prompt List": "Prompt List (DS, CR-compatible id)",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
