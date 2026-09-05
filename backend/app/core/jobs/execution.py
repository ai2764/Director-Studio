"""Execution-adapter contracts and registry for durable pipeline jobs."""

from __future__ import annotations

from typing import Any, Iterable, Protocol


class ExecutionAdapter(Protocol):
    """Protocol marker for one job execution environment."""

    id: str


class ExecutionAdapterRegistry:
    def __init__(self, adapters: Iterable[ExecutionAdapter] = ()) -> None:
        self._adapters: dict[str, ExecutionAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: ExecutionAdapter) -> ExecutionAdapter:
        adapter_id = str(adapter.id or "").strip()
        if not adapter_id:
            raise ValueError("execution adapter id is required")
        if adapter_id in self._adapters:
            raise ValueError(f"duplicate execution adapter: {adapter_id}")
        self._adapters[adapter_id] = adapter
        return adapter

    def resolve(self, pipeline: Any, *, job: Any | None = None) -> ExecutionAdapter:
        resolver = getattr(pipeline, "execution_adapter_id_for_job", None)
        adapter_id = str(
            resolver(job) if job is not None and callable(resolver)
            else getattr(pipeline, "execution_adapter_id", "") or ""
        ).strip()
        try:
            return self._adapters[adapter_id]
        except KeyError as exc:
            known = ", ".join(sorted(self._adapters)) or "(none)"
            raise KeyError(
                f"Unknown execution adapter '{adapter_id}'. Known: {known}"
            ) from exc
