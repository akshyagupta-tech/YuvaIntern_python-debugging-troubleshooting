"""Defensive arithmetic, resource ownership, and fault-tolerance primitives."""

from __future__ import annotations

import copy
import math
import socket
import threading
import time
from collections import deque
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    IO,
    List,
    Literal,
    Optional,
    TypeVar,
    Union,
)

from diagnostics import (
    CircuitOpenError,
    MathDomainFault,
    ResourceLeakError,
    RuntimeValidationError,
    StateCorruptionError,
    build_exception_report,
    validate_arguments,
)

_MISSING = object()
_T = TypeVar("_T")


def append_record_safe(
    record: str,
    store: Optional[List[str]] = None,
) -> List[str]:
    """Append to an explicit list or create a fresh list for this call."""
    if not isinstance(record, str):
        raise StateCorruptionError("Record must be a string.")
    target = [] if store is None else store
    if not isinstance(target, list) or not all(
        isinstance(item, str) for item in target
    ):
        raise StateCorruptionError("Record store must be a list of strings.")
    target.append(record)
    return target


class SafeArithmetic:
    """Arithmetic operations guarded against invalid and non-finite inputs."""

    @staticmethod
    def sanitize(value: Union[int, float]) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MathDomainFault("Numeric input must be an integer or float.")
        try:
            result = float(value)
        except (OverflowError, ValueError) as error:
            raise MathDomainFault(
                "Numeric input is outside supported range."
            ) from error
        if not math.isfinite(result):
            raise MathDomainFault("NaN and infinity are not permitted.")
        return result

    @classmethod
    @validate_arguments
    def divide(cls, numerator: float, denominator: float) -> float:
        numerator = cls.sanitize(numerator)
        denominator = cls.sanitize(denominator)
        if denominator == 0.0:
            raise MathDomainFault("Division by zero is not permitted.")
        result = numerator / denominator
        return cls.sanitize(result)

    @classmethod
    @validate_arguments
    def square_root(cls, value: float) -> float:
        value = cls.sanitize(value)
        if value < 0:
            raise MathDomainFault("Square root requires a non-negative value.")
        return cls.sanitize(math.sqrt(value))

    @classmethod
    def average(cls, values: List[Union[int, float]]) -> float:
        if not isinstance(values, list) or not values:
            raise MathDomainFault("Average requires a non-empty numeric list.")
        sanitized = [cls.sanitize(value) for value in values]
        return cls.sanitize(math.fsum(sanitized) / len(sanitized))


class ManagedFile:
    """Own one file handle and close it on every context exit."""

    def __init__(
        self,
        path: Union[str, Path],
        mode: str = "r",
        encoding: Optional[str] = "utf-8",
    ) -> None:
        if not isinstance(mode, str) or not mode:
            raise ResourceLeakError("File mode must be a non-empty string.")
        self.path = Path(path)
        self.mode = mode
        self.encoding = encoding
        self._file: Optional[IO[Any]] = None

    @property
    def closed(self) -> bool:
        return self._file is None or self._file.closed

    def __enter__(self) -> IO[Any]:
        if self._file is not None and not self._file.closed:
            raise ResourceLeakError("Managed file is already open.")
        try:
            if "b" in self.mode:
                self._file = self.path.open(self.mode)
            else:
                self._file = self.path.open(
                    self.mode,
                    encoding=self.encoding,
                )
        except OSError:
            self._file = None
            raise
        return self._file

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> Literal[False]:
        file = self._file
        self._file = None
        if file is not None and not file.closed:
            try:
                file.close()
            except OSError as error:
                if exc_type is None:
                    raise ResourceLeakError(
                        "Unable to close managed file."
                    ) from error
        return False


class ManagedSocket:
    """Own an already-created socket and close it deterministically on exit."""

    def __init__(self, connection: socket.socket) -> None:
        if not isinstance(connection, socket.socket):
            raise ResourceLeakError("ManagedSocket requires a socket object.")
        self._socket: Optional[socket.socket] = connection

    @property
    def closed(self) -> bool:
        return self._socket is None or self._socket.fileno() < 0

    def __enter__(self) -> socket.socket:
        if self._socket is None or self._socket.fileno() < 0:
            raise ResourceLeakError("Managed socket is not open.")
        return self._socket

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> Literal[False]:
        connection = self._socket
        self._socket = None
        if connection is not None and connection.fileno() >= 0:
            try:
                connection.close()
            except OSError as error:
                if exc_type is None:
                    raise ResourceLeakError(
                        "Unable to close managed socket."
                    ) from error
        return False


class ThreadSafeStateStore:
    """A synchronized key-value store that isolates values by copying."""

    def __init__(self) -> None:
        self._values: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def set(self, key: str, value: object) -> None:
        self._validate_key(key)
        try:
            safe_value = copy.deepcopy(value)
        except Exception as error:
            raise StateCorruptionError(
                "State value cannot be copied safely."
            ) from error
        with self._lock:
            self._values[key] = safe_value

    def get(self, key: str, default: object = _MISSING) -> Any:
        self._validate_key(key)
        with self._lock:
            if key not in self._values:
                if default is _MISSING:
                    raise KeyError(key)
                value = default
            else:
                value = self._values[key]
            try:
                return copy.deepcopy(value)
            except Exception as error:
                raise StateCorruptionError(
                    "State value cannot be copied safely."
                ) from error

    def update(
        self,
        key: str,
        transform: Callable[[Any], _T],
        initial: object = None,
    ) -> _T:
        """Apply a transform atomically and return an isolated copy."""
        self._validate_key(key)
        if not callable(transform):
            raise StateCorruptionError("State transform must be callable.")
        with self._lock:
            current = self._values.get(key, initial)
            try:
                updated = transform(copy.deepcopy(current))
                stored = copy.deepcopy(updated)
                result = copy.deepcopy(updated)
            except Exception as error:
                raise StateCorruptionError(
                    "Atomic state update could not be copied."
                ) from error
            self._values[key] = stored
            return result

    def delete(self, key: str) -> bool:
        self._validate_key(key)
        with self._lock:
            return self._values.pop(key, _MISSING) is not _MISSING

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            try:
                return copy.deepcopy(self._values)
            except Exception as error:
                raise StateCorruptionError("State snapshot failed.") from error

    @staticmethod
    def _validate_key(key: str) -> None:
        if not isinstance(key, str) or not key.strip():
            raise StateCorruptionError("State keys must be non-empty strings.")


class CircuitState(str, Enum):
    """Circuit breaker operational state."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Trip after consecutive errors and permit one probe after cooldown."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        if not name.strip():
            raise ValueError("Circuit name must not be empty.")
        if failure_threshold < 1:
            raise ValueError("Failure threshold must be positive.")
        if not math.isfinite(recovery_timeout) or recovery_timeout <= 0:
            raise ValueError(
                "Recovery timeout must be a positive finite value."
            )
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._clock = clock if clock is not None else time.monotonic
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probe_in_flight = False
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failures

    def call(
        self, operation: Callable[..., _T], *args: Any, **kwargs: Any
    ) -> _T:
        if not callable(operation):
            raise TypeError("Circuit operation must be callable.")
        is_probe = self._acquire_permission()
        try:
            result = operation(*args, **kwargs)
        except Exception:
            with self._lock:
                self._failures += 1
                if is_probe or self._failures >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    self._opened_at = self._clock()
                self._probe_in_flight = False
            raise
        with self._lock:
            self._failures = 0
            self._probe_in_flight = False
            self._state = CircuitState.CLOSED
        return result

    def _acquire_permission(self) -> bool:
        with self._lock:
            now = self._clock()
            if self._state is CircuitState.OPEN:
                if now - self._opened_at < self.recovery_timeout:
                    raise CircuitOpenError(
                        f"Circuit {self.name!r} is open; retry after cooldown."
                    )
                self._state = CircuitState.HALF_OPEN
            if self._state is CircuitState.HALF_OPEN:
                if self._probe_in_flight:
                    raise CircuitOpenError(
                        f"Circuit {self.name!r} already has a recovery probe."
                    )
                self._probe_in_flight = True
                return True
            return False


class ReliabilityMetrics:
    """Thread-safe fault, recovery, MTTR, frequency, and catch counters."""

    def __init__(self, clock: Optional[Callable[[], float]] = None) -> None:
        self._clock = clock if clock is not None else time.monotonic
        self._fault_times: deque[float] = deque(maxlen=10_000)
        self._recovery_durations: List[float] = []
        self._caught_count = 0
        self._lock = threading.Lock()

    def record_fault(self) -> None:
        with self._lock:
            self._fault_times.append(self._clock())

    def record_caught(self) -> None:
        with self._lock:
            self._caught_count += 1

    def record_recovery(self, duration_seconds: float) -> None:
        if (
            isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, (int, float))
            or not math.isfinite(duration_seconds)
            or duration_seconds < 0
        ):
            raise ValueError(
                "Recovery duration must be finite and non-negative."
            )
        with self._lock:
            self._recovery_durations.append(float(duration_seconds))

    def snapshot(self) -> Dict[str, Union[int, float]]:
        with self._lock:
            fault_times = list(self._fault_times)
            caught_count = self._caught_count
            recoveries = list(self._recovery_durations)
        elapsed = (
            max(0.0, self._clock() - fault_times[0]) if fault_times else 0.0
        )
        frequency = len(fault_times) * 60.0 / elapsed if elapsed > 0 else 0.0
        return {
            "fault_count": len(fault_times),
            "caught_count": caught_count,
            "mttr_seconds": (
                math.fsum(recoveries) / len(recoveries) if recoveries else 0.0
            ),
            "fault_frequency_per_minute": frequency,
            "catch_ratio": (
                min(caught_count, len(fault_times)) / len(fault_times)
                if fault_times
                else 0.0
            ),
        }


class DebugSession:
    """Step through captured frames from one predefined synthetic exception."""

    def __init__(self, scenario: str, report: Dict[str, Any]) -> None:
        self.scenario = scenario
        self.report = report
        self._frames = list(report.get("frames", []))
        self._index = 0

    @property
    def done(self) -> bool:
        return self._index >= len(self._frames)

    def step(self) -> Optional[Dict[str, Any]]:
        if self.done:
            return None
        frame = self._frames[self._index]
        self._index += 1
        return frame


def create_debug_session(scenario: str) -> DebugSession:
    """Capture a traceback for an allow-listed synthetic edge-case scenario."""
    scenarios: Dict[str, Callable[[], object]] = {
        "division_by_zero": lambda: SafeArithmetic.divide(1, 0),
        "negative_square_root": lambda: SafeArithmetic.square_root(-1),
        "non_finite_average": lambda: SafeArithmetic.average([1, math.nan]),
    }
    operation = scenarios.get(scenario)
    if operation is None:
        raise ValueError("Unknown synthetic debug scenario.")
    try:
        operation()
    except Exception as error:
        report = build_exception_report(
            type(error), error, error.__traceback__
        )
        report["scenario"] = scenario
        report["exception_type"] = type(error).__name__
        return DebugSession(scenario, report)
    raise RuntimeValidationError(
        "Synthetic scenario did not raise as expected."
    )
