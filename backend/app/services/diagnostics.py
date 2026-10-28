"""Local, content-free processing and export diagnostics."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any

METRICS_VERSION = 1


def _peak_resident_set_mb() -> float:
    """Read peak process memory when the platform exposes resource usage."""
    try:
        resource_module = import_module("resource")
        usage = float(resource_module.getrusage(resource_module.RUSAGE_SELF).ru_maxrss)
        return round(usage / (1024 * 1024 if os.name == "posix" and usage > 10**8 else 1024), 2)
    except (ImportError, AttributeError, OSError, ValueError):
        return 0.0


@dataclass(slots=True)
class MetricsCollector:
    """Collect numeric diagnostics without retaining transcript, OCR, or path content."""

    project_id: str
    operation_id: str
    operation: str
    strategy: str
    counters: dict[str, int | float] = field(default_factory=dict)
    stage_durations_ms: dict[str, int] = field(default_factory=dict)
    _stage_started_at: dict[str, float] = field(default_factory=dict, repr=False)

    def start_stage(self, stage: str) -> None:
        """Start timing one named stage before its bounded work begins."""
        self._stage_started_at[stage] = time.perf_counter()

    def finish_stage(self, stage: str, logger: logging.Logger | None = None) -> int:
        """Finish one stage timer and optionally emit its content-free JSON event."""
        started_at = self._stage_started_at.pop(stage, None)
        duration_ms = 0 if started_at is None else round((time.perf_counter() - started_at) * 1000)
        self.stage_durations_ms[stage] = duration_ms
        if logger is not None:
            payload = {
                "event": f"{stage}_completed",
                "projectId": self.project_id,
                "operationId": self.operation_id,
                "strategy": self.strategy,
                "durationMs": duration_ms,
                **self.counters,
            }
            logger.info(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return duration_ms

    def set(self, name: str, value: int | float) -> None:
        """Set one numeric metric after its authoritative stage completes."""
        self.counters[name] = value

    def increment(self, name: str, amount: int = 1) -> None:
        """Increment one integer counter during streaming work."""
        self.counters[name] = int(self.counters.get(name, 0)) + amount

    def snapshot(self, terminal_status: str | None = None) -> dict[str, Any]:
        """Build the safe serializable diagnostic contract used by SQLite and files."""
        result: dict[str, Any] = {
            "metricsVersion": METRICS_VERSION,
            "operation": self.operation,
            "strategy": self.strategy,
            **self.counters,
            "stageDurationsMs": dict(sorted(self.stage_durations_ms.items())),
            "peakResidentSetMb": _peak_resident_set_mb(),
        }
        if terminal_status is not None:
            result["terminalStatus"] = terminal_status
        return result

    def write_atomic(self, destination: Path, terminal_status: str) -> Path:
        """Persist one diagnostic snapshot atomically inside the project workspace."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(f".{destination.name}.partial")
        payload = {
            "projectId": self.project_id,
            "operationId": self.operation_id,
            **self.snapshot(terminal_status),
        }
        try:
            partial.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(partial, destination)
        finally:
            partial.unlink(missing_ok=True)
        return destination
