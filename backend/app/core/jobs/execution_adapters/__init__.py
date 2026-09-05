"""Protocol-specific durable job execution adapters."""

from .comfy import ComfyExecutionAdapter
from .comfy_mcp import ComfyMcpExecutionAdapter
from .external import ExternalExecutionAdapter
from .h3_api import H3ApiExecutionAdapter

__all__ = [
    "ComfyExecutionAdapter",
    "ComfyMcpExecutionAdapter",
    "ExternalExecutionAdapter",
    "H3ApiExecutionAdapter",
]
