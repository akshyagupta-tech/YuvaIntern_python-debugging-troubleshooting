"""Structured diagnostics, safe traceback inspection, and runtime telemetry."""

from __future__ import annotations

import contextvars
import datetime as dt
import inspect
import io
import json
import logging
import logging.handlers
import math
import re
import sys
import time
import traceback
from collections import Counter, deque
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from threading import RLock
from types import TracebackType, UnionType
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    ParamSpec,
    TextIO,
    TypeVar,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)


class ReliabilityError(Exception):
    """Base class for controlled reliability and diagnostic failures."""


class StateCorruptionError(ReliabilityError):
    """Raised when state cannot be safely copied or validated."""


class MathDomainFault(ReliabilityError):
    """Raised when numeric input is invalid or outside the operation domain."""


class ResourceLeakError(ReliabilityError):
    """Raised when a managed resource cannot be deterministically released."""


class CircuitOpenError(ReliabilityError):
    """Raised when a circuit breaker prevents a downstream call."""


class RuntimeValidationError(ReliabilityError, TypeError):
    """Raised when a call argument violates its type annotation."""


_SECRET_KEY_PATTERN = re.compile(
    r"password|passwd|secret|token|credential|api[_-]?key|"
    r"authorization|cookie|env",
    re.IGNORECASE,
)
_SECRET_TEXT_PATTERN = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)"
    r"\s*[:=]\s*(['\"]?)[^\s,;\]\}\"']+"
)


def _redact_text(value: str) -> str:
    return _SECRET_TEXT_PATTERN.sub(r"\1=<redacted>", value)[:2_000]


def safe_snapshot(value: object, key: str = "", depth: int = 0) -> object:
    """Return a bounded JSON-safe snapshot with sensitive keys redacted."""
    if _SECRET_KEY_PATTERN.search(key):
        return "<redacted>"
    if depth > 3:
        return "<max-depth>"
    if value is None or isinstance(value, (bool, int, str)):
        return _redact_text(value) if isinstance(value, str) else value
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, Path):
        return value.name
    if isinstance(value, dict):
        return {
            str(item_key): safe_snapshot(item_value, str(item_key), depth + 1)
            for item_key, item_value in list(value.items())[:20]
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [
            safe_snapshot(item, key, depth + 1) for item in list(value)[:20]
        ]
    return {
        "type": type(value).__name__,
        "memory_id": hex(id(value)),
        "value": f"<{type(value).__name__} instance>",
    }


def _scrub_known_secrets(value: object, secrets_found: List[str]) -> object:
    if isinstance(value, str):
        cleaned = _redact_text(value)
        for secret in secrets_found:
            cleaned = cleaned.replace(secret, "<redacted>")
        return cleaned
    if isinstance(value, list):
        return [_scrub_known_secrets(item, secrets_found) for item in value]
    if isinstance(value, dict):
        return {
            key: _scrub_known_secrets(item, secrets_found)
            for key, item in value.items()
        }
    return value


class StructuredJsonFormatter(logging.Formatter):
    """Format log records as compact ISO 8601 JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = dt.datetime.fromtimestamp(
            record.created,
            tz=dt.timezone.utc,
        ).isoformat()
        message = _redact_text(record.getMessage())
        payload = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "context": safe_snapshot(getattr(record, "context", {})),
        }
        if record.exc_info:
            payload["exception"] = _redact_text(
                self.formatException(record.exc_info)
            )
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class DiagnosticLogger:
    """Send structured records to a JSON stream and a syslog handler."""

    def __init__(
        self,
        name: str = "enterprise.reliability",
        json_stream: Optional[TextIO] = None,
        syslog_handler: Optional[logging.Handler] = None,
    ) -> None:
        self.json_stream = (
            json_stream if json_stream is not None else io.StringIO()
        )
        self._logger = logging.getLogger(f"{name}.{id(self)}")
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False
        self._json_handler = logging.StreamHandler(self.json_stream)
        self._json_handler.setFormatter(StructuredJsonFormatter())
        self._logger.addHandler(self._json_handler)

        self._syslog_handler = (
            syslog_handler
            if syslog_handler is not None
            else logging.handlers.SysLogHandler(address=("localhost", 514))
        )
        self._syslog_handler.setFormatter(StructuredJsonFormatter())
        self._logger.addHandler(self._syslog_handler)

    def log(self, level: int, message: str, **context: object) -> None:
        self._logger.log(
            level,
            _redact_text(message),
            extra={"context": safe_snapshot(context)},
        )

    def debug(self, message: str, **context: object) -> None:
        self.log(logging.DEBUG, message, **context)

    def info(self, message: str, **context: object) -> None:
        self.log(logging.INFO, message, **context)

    def warning(self, message: str, **context: object) -> None:
        self.log(logging.WARNING, message, **context)

    def error(self, message: str, **context: object) -> None:
        self.log(logging.ERROR, message, **context)

    def critical(self, message: str, **context: object) -> None:
        self.log(logging.CRITICAL, message, **context)

    @property
    def json_output(self) -> str:
        """Return captured JSON when the stream supports getvalue."""
        getter = getattr(self.json_stream, "getvalue", None)
        return getter() if callable(getter) else ""

    def close(self) -> None:
        for handler in (self._json_handler, self._syslog_handler):
            self._logger.removeHandler(handler)
            handler.close()


_DEFAULT_LOGGER: Optional[DiagnosticLogger] = None


def build_exception_report(
    exception_type: type[BaseException],
    exception: BaseException,
    tb: Optional[TracebackType],
) -> Dict[str, Any]:
    """Create a redacted traceback report with source and local snapshots."""
    frames: List[Dict[str, Any]] = []
    secrets_found: List[str] = []
    current = tb
    while current is not None:
        for name, value in current.tb_frame.f_locals.items():
            if (
                _SECRET_KEY_PATTERN.search(name)
                and isinstance(value, str)
                and len(value) >= 4
            ):
                secrets_found.append(value)
        current = current.tb_next

    current = tb
    while current is not None:
        frame = current.tb_frame
        locals_snapshot = {
            name: {
                "type": type(value).__name__,
                "value": safe_snapshot(value, name),
                "memory_id": hex(id(value)),
            }
            for name, value in list(frame.f_locals.items())[:40]
        }
        frames.append(
            {
                "file": Path(frame.f_code.co_filename).name,
                "function": frame.f_code.co_name,
                "line": current.tb_lineno,
                "source": _redact_text(
                    traceback.extract_tb(current, limit=1)[0].line or ""
                ),
                "locals": locals_snapshot,
            }
        )
        current = current.tb_next
    report: Dict[str, Any] = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "exception_type": exception_type.__name__,
        "message": _redact_text(str(exception)),
        "frames": frames,
    }
    scrubbed = _scrub_known_secrets(report, secrets_found)
    return cast(Dict[str, Any], scrubbed)


def install_exception_hook(
    logger: DiagnosticLogger,
    output: Optional[TextIO] = None,
) -> Callable[..., Any]:
    """Install the safe hook and return the prior hook for restoration."""
    destination = output if output is not None else sys.stderr
    previous_hook = sys.excepthook

    def hook(
        exception_type: type[BaseException],
        exception: BaseException,
        tb: Optional[TracebackType],
    ) -> None:
        report = build_exception_report(exception_type, exception, tb)
        destination.write(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
        logger.critical("Unhandled exception", diagnostic=report)

    sys.excepthook = hook
    return previous_hook


def _matches_type(value: object, expected: object) -> bool:
    if expected is Any or expected is inspect.Signature.empty:
        return True
    origin = get_origin(expected)
    arguments = get_args(expected)
    if origin in (Union, UnionType):
        return any(_matches_type(value, item) for item in arguments)
    if origin is list:
        return isinstance(value, list) and (
            not arguments
            or all(_matches_type(item, arguments[0]) for item in value)
        )
    if origin is dict:
        return isinstance(value, dict) and (
            not arguments
            or all(
                _matches_type(key, arguments[0])
                and _matches_type(item, arguments[1])
                for key, item in value.items()
            )
        )
    if origin is tuple:
        if not isinstance(value, tuple):
            return False
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            return all(_matches_type(item, arguments[0]) for item in value)
        return len(value) == len(arguments) and all(
            _matches_type(item, item_type)
            for item, item_type in zip(value, arguments)
        )
    if origin is not None and arguments:
        return True
    if expected is int and isinstance(value, bool):
        return False
    if expected is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(expected, type):
        return isinstance(value, expected)
    return True


P = ParamSpec("P")
R = TypeVar("R")


def validate_arguments(
    function: Callable[P, R],
) -> Callable[P, R]:
    """Validate annotated positional and keyword arguments at call time."""
    signature = inspect.signature(function)
    try:
        hints = get_type_hints(function)
    except (NameError, TypeError):
        hints = function.__annotations__

    @wraps(function)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            bound = signature.bind(*args, **kwargs)
        except TypeError as error:
            raise RuntimeValidationError(str(error)) from error
        bound.apply_defaults()
        for name, value in bound.arguments.items():
            expected = hints.get(name, inspect.Signature.empty)
            if not _matches_type(value, expected):
                raise RuntimeValidationError(
                    f"Argument {name!r} must match {expected!r}; "
                    f"received {type(value).__name__}."
                )
        return function(*args, **kwargs)

    return wrapper


@dataclass(frozen=True)
class TraceEvent:
    """One safe call snapshot produced by :func:`trace_calls`."""

    timestamp: str
    function: str
    duration_ms: float
    call_number: int
    recursion_depth: int
    arguments: Dict[str, object]
    error_type: Optional[str]


@dataclass(frozen=True)
class TraceSummary:
    """Aggregated counters for a traced function."""

    function: str
    call_count: int
    error_count: int
    max_recursion_depth: int
    total_duration_ms: float


class TraceRegistry:
    """Thread-safe bounded registry for function call telemetry."""

    def __init__(self, max_events: int = 500) -> None:
        if max_events < 1:
            raise ValueError("max_events must be positive.")
        self._events: deque[TraceEvent] = deque(maxlen=max_events)
        self._counts: Counter[str] = Counter()
        self._errors: Counter[str] = Counter()
        self._depths: Counter[str] = Counter()
        self._durations: Dict[str, float] = {}
        self._lock = RLock()

    def record(self, event: TraceEvent) -> None:
        with self._lock:
            self._events.append(event)
            self._counts[event.function] += 1
            self._depths[event.function] = max(
                self._depths[event.function],
                event.recursion_depth,
            )
            self._durations[event.function] = (
                self._durations.get(event.function, 0.0) + event.duration_ms
            )
            if event.error_type is not None:
                self._errors[event.function] += 1

    def events(self, function: Optional[str] = None) -> List[TraceEvent]:
        with self._lock:
            events = list(self._events)
        return [
            event
            for event in events
            if function is None or event.function == function
        ]

    def summary(self, function: str) -> TraceSummary:
        with self._lock:
            return TraceSummary(
                function=function,
                call_count=self._counts[function],
                error_count=self._errors[function],
                max_recursion_depth=self._depths[function],
                total_duration_ms=self._durations.get(function, 0.0),
            )

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
            self._counts.clear()
            self._errors.clear()
            self._depths.clear()
            self._durations.clear()


GLOBAL_TRACE_REGISTRY = TraceRegistry()


def _argument_snapshot(
    function: Callable[..., Any],
    args: tuple[object, ...],
    kwargs: Dict[str, object],
) -> Dict[str, object]:
    try:
        bound = inspect.signature(function).bind_partial(*args, **kwargs)
        snapshot = safe_snapshot(dict(bound.arguments))
        return snapshot if isinstance(snapshot, dict) else {}
    except TypeError:
        return {"arguments": "<unavailable>"}


def trace_calls(
    *,
    registry: Optional[TraceRegistry] = None,
    logger: Optional[DiagnosticLogger] = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Measure latency, calls, recursion, and redacted argument snapshots."""
    target_registry = (
        registry if registry is not None else GLOBAL_TRACE_REGISTRY
    )

    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        function_name = function.__name__
        depth: contextvars.ContextVar[int] = contextvars.ContextVar(
            f"trace_depth_{id(function)}",
            default=0,
        )

        if inspect.iscoroutinefunction(function):

            @wraps(function)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                current_depth = depth.get() + 1
                token = depth.set(current_depth)
                started = time.perf_counter()
                error_type: Optional[str] = None
                try:
                    return await function(
                        *args,
                        **kwargs,
                    )  # type: ignore[misc]
                except Exception as error:
                    error_type = type(error).__name__
                    raise
                finally:
                    event = TraceEvent(
                        timestamp=dt.datetime.now(dt.timezone.utc).isoformat(),
                        function=function_name,
                        duration_ms=(time.perf_counter() - started) * 1_000,
                        call_number=target_registry.summary(
                            function_name
                        ).call_count
                        + 1,
                        recursion_depth=current_depth,
                        arguments=_argument_snapshot(function, args, kwargs),
                        error_type=error_type,
                    )
                    target_registry.record(event)
                    if error_type is not None and logger is not None:
                        logger.error(
                            "Traced function failed",
                            function=function_name,
                            error_type=error_type,
                        )
                    depth.reset(token)

            return async_wrapper  # type: ignore[return-value]

        @wraps(function)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            current_depth = depth.get() + 1
            token = depth.set(current_depth)
            started = time.perf_counter()
            error_type: Optional[str] = None
            try:
                return function(*args, **kwargs)
            except Exception as error:
                error_type = type(error).__name__
                raise
            finally:
                event = TraceEvent(
                    timestamp=dt.datetime.now(dt.timezone.utc).isoformat(),
                    function=function_name,
                    duration_ms=(time.perf_counter() - started) * 1_000,
                    call_number=target_registry.summary(
                        function_name
                    ).call_count
                    + 1,
                    recursion_depth=current_depth,
                    arguments=_argument_snapshot(function, args, kwargs),
                    error_type=error_type,
                )
                target_registry.record(event)
                if error_type is not None and logger is not None:
                    logger.error(
                        "Traced function failed",
                        function=function_name,
                        error_type=error_type,
                    )
                depth.reset(token)

        return wrapper

    return decorate
