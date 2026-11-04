"""Heap allocation snapshots and weak-reference leak diagnostics."""

from __future__ import annotations

import json
import threading
import tracemalloc
import weakref
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union


class MemoryProfiler:
    """Capture heap deltas and observe objects without retaining them."""

    def __init__(self, top_limit: int = 10, sample_limit: int = 100) -> None:
        if top_limit < 1 or sample_limit < 1:
            raise ValueError("Profiler limits must be positive.")
        self.top_limit = top_limit
        self.sample_limit = sample_limit
        self._snapshots: List[Dict[str, Any]] = []
        self._samples: List[Dict[str, Union[int, str]]] = []
        self._tracked: List[
            Tuple[str, str, str, weakref.ReferenceType[Any]]
        ] = []
        self._previous: Optional[tracemalloc.Snapshot] = None
        self._owns_tracing = False
        self._lock = threading.RLock()

    @property
    def running(self) -> bool:
        return tracemalloc.is_tracing()

    def start(self) -> None:
        """Start tracemalloc only when no other owner started it."""
        with self._lock:
            if not tracemalloc.is_tracing():
                tracemalloc.start(25)
                self._owns_tracing = True

    def snapshot(self, label: str = "snapshot") -> Dict[str, Any]:
        """Capture current/peak heap and top allocation deltas."""
        if not isinstance(label, str) or not label.strip():
            raise ValueError("Snapshot label must not be empty.")
        self.start()
        with self._lock:
            current_bytes, peak_bytes = tracemalloc.get_traced_memory()
            current_snapshot = tracemalloc.take_snapshot()
            baseline = self._previous
            source_stats = (
                current_snapshot.compare_to(baseline, "lineno")
                if baseline is not None
                else current_snapshot.statistics("lineno")
            )
            top_allocations = [
                {
                    "file": Path(statistic.traceback[0].filename).name,
                    "line": statistic.traceback[0].lineno,
                    "size_diff_bytes": getattr(
                        statistic,
                        "size_diff",
                        statistic.size,
                    ),
                    "count_diff": getattr(
                        statistic,
                        "count_diff",
                        statistic.count,
                    ),
                }
                for statistic in source_stats[: self.top_limit]
            ]
            previous_current = (
                self._snapshots[-1]["current_bytes"] if self._snapshots else 0
            )
            result: Dict[str, Any] = {
                "label": label.strip(),
                "current_bytes": current_bytes,
                "peak_bytes": peak_bytes,
                "delta_bytes": current_bytes - previous_current,
                "top_allocations": top_allocations,
            }
            self._snapshots.append(result)
            self._samples.append(
                {
                    "label": result["label"],
                    "current_bytes": current_bytes,
                    "peak_bytes": peak_bytes,
                }
            )
            self._snapshots = self._snapshots[-self.sample_limit:]
            self._samples = self._samples[-self.sample_limit:]
            self._previous = current_snapshot
            return result

    def track_object(self, value: object, label: str) -> None:
        """Observe an object's lifetime via a weak reference."""
        if not isinstance(label, str) or not label.strip():
            raise ValueError("Tracked object label must not be empty.")
        try:
            reference = weakref.ref(value)
        except TypeError as error:
            raise TypeError(
                f"{type(value).__name__} does not support weak references."
            ) from error
        with self._lock:
            self._tracked.append(
                (
                    label.strip(),
                    type(value).__name__,
                    hex(id(value)),
                    reference,
                )
            )

    def live_objects(self) -> List[Dict[str, str]]:
        """Return live tracked objects without retaining the objects."""
        with self._lock:
            return [
                {"label": label, "type": type_name, "memory_id": memory_id}
                for label, type_name, memory_id, reference in self._tracked
                if reference() is not None
            ]

    def heap_series(self) -> List[Dict[str, Union[int, str]]]:
        with self._lock:
            return [dict(sample) for sample in self._samples]

    def export_json(self) -> str:
        """Export heap snapshots and current weak-reference observations."""
        with self._lock:
            payload = {
                "tracemalloc_running": tracemalloc.is_tracing(),
                "snapshot_count": len(self._snapshots),
                "snapshots": list(self._snapshots),
                "live_objects": self.live_objects(),
            }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def stop(self) -> None:
        """Stop tracing only if this profiler started the global tracer."""
        with self._lock:
            if self._owns_tracing and tracemalloc.is_tracing():
                tracemalloc.stop()
            self._owns_tracing = False

    def __enter__(self) -> MemoryProfiler:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> Literal[False]:
        self.stop()
        return False
