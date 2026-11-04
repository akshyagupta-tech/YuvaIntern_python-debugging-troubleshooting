"""Reliability, diagnostics, resource, and defensive-state regression tests."""

import gc
import io
import json
import logging
import math
import socket
import sys
import tempfile
import threading
import unittest
import weakref
from pathlib import Path

from diagnostics import (
    CircuitOpenError,
    DiagnosticLogger,
    ReliabilityError,
    RuntimeValidationError,
    TraceRegistry,
    build_exception_report,
    install_exception_hook,
    trace_calls,
    validate_arguments,
)
from profiler import MemoryProfiler
from reliability_core import (
    CircuitBreaker,
    CircuitState,
    ManagedFile,
    ManagedSocket,
    MathDomainFault,
    ReliabilityMetrics,
    ResourceLeakError,
    SafeArithmetic,
    StateCorruptionError,
    ThreadSafeStateStore,
    append_record_safe,
    create_debug_session,
)


class RecordingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class ReliabilityTests(unittest.TestCase):
    def test_domain_exceptions_share_reliability_base(self) -> None:
        self.assertTrue(issubclass(MathDomainFault, ReliabilityError))
        self.assertTrue(issubclass(ResourceLeakError, ReliabilityError))
        self.assertTrue(issubclass(StateCorruptionError, ReliabilityError))
        self.assertTrue(issubclass(CircuitOpenError, ReliabilityError))

    def test_sentinel_append_isolates_calls_and_preserves_explicit_store(
        self,
    ) -> None:
        first = append_record_safe("first")
        second = append_record_safe("second")
        explicit: list[str] = []

        self.assertEqual(first, ["first"])
        self.assertEqual(second, ["second"])
        self.assertIsNot(first, second)
        self.assertEqual(append_record_safe("third", explicit), ["third"])
        self.assertEqual(explicit, ["third"])

    def test_arithmetic_guards_zero_domain_and_non_finite_values(self) -> None:
        self.assertEqual(SafeArithmetic.divide(9, 3), 3.0)
        self.assertEqual(SafeArithmetic.square_root(9), 3.0)
        self.assertEqual(SafeArithmetic.average([1, 2, 3]), 2.0)
        with self.assertRaises(MathDomainFault):
            SafeArithmetic.divide(1, 0)
        with self.assertRaises(MathDomainFault):
            SafeArithmetic.square_root(-1)
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaises(MathDomainFault):
                    SafeArithmetic.sanitize(value)
        with self.assertRaises(MathDomainFault):
            SafeArithmetic.average([])

    def test_managed_file_closes_when_body_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "managed.txt"
            resource = ManagedFile(path, "w", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                with resource as file:
                    file.write("written")
                    raise RuntimeError("synthetic failure")

            self.assertTrue(resource.closed)
            self.assertEqual(path.read_text(encoding="utf-8"), "written")

    def test_managed_socket_closes_when_body_raises(self) -> None:
        endpoint, peer = socket.socketpair()
        resource = ManagedSocket(endpoint)
        try:
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                with resource as managed_socket:
                    self.assertGreaterEqual(managed_socket.fileno(), 0)
                    raise RuntimeError("synthetic failure")
            self.assertTrue(resource.closed)
            self.assertEqual(endpoint.fileno(), -1)
        finally:
            peer.close()

    def test_structured_logger_writes_json_and_syslog_sinks(self) -> None:
        stream = io.StringIO()
        syslog = RecordingHandler()
        logger = DiagnosticLogger(
            name="test.reliability",
            json_stream=stream,
            syslog_handler=syslog,
        )
        logger.info("request failed", request_id="req-7", attempt=2)
        record = json.loads(stream.getvalue())

        self.assertEqual(record["level"], "INFO")
        self.assertEqual(record["message"], "request failed")
        self.assertEqual(record["context"]["request_id"], "req-7")
        self.assertRegex(record["timestamp"], r"\+00:00|Z$")
        self.assertEqual(len(syslog.records), 1)
        logger.close()

    def test_exception_inspector_redacts_secrets_and_includes_frames(
        self,
    ) -> None:
        try:
            password = "never-print-this"
            api_token = "token-never-print-this"
            harmless_value = 27
            self.assertTrue(password and api_token and harmless_value)
            raise ValueError("synthetic input fault")
        except ValueError as error:
            report = build_exception_report(
                type(error),
                error,
                error.__traceback__,
            )

        encoded = json.dumps(report)
        self.assertIn("synthetic input fault", encoded)
        self.assertIn("harmless_value", encoded)
        self.assertNotIn(password, encoded)
        self.assertNotIn(api_token, encoded)
        self.assertIn("<redacted>", encoded)
        self.assertTrue(report["frames"])
        self.assertIn(
            "memory_id", report["frames"][-1]["locals"]["harmless_value"]
        )

    def test_exception_hook_logs_redacted_structured_report(self) -> None:
        stream = io.StringIO()
        syslog = RecordingHandler()
        logger = DiagnosticLogger(
            name="test.exception.hook",
            json_stream=stream,
            syslog_handler=syslog,
        )
        old_hook = sys.excepthook
        install_exception_hook(logger, output=io.StringIO())
        try:
            try:
                secret = "hook-secret-value"
                self.assertTrue(secret)
                raise LookupError("hook synthetic error")
            except LookupError as error:
                sys.excepthook(type(error), error, error.__traceback__)
        finally:
            sys.excepthook = old_hook
            logger.close()

        self.assertNotIn("hook-secret-value", stream.getvalue())
        self.assertIn("hook synthetic error", stream.getvalue())
        self.assertEqual(len(syslog.records), 1)

    def test_exception_message_cannot_echo_a_secret_local(self) -> None:
        try:
            secret_token = "sensitive-value-from-local"
            raise RuntimeError(secret_token)
        except RuntimeError as error:
            report = build_exception_report(
                type(error),
                error,
                error.__traceback__,
            )

        self.assertNotIn("sensitive-value-from-local", json.dumps(report))

    def test_runtime_validator_checks_nested_types_before_body(self) -> None:
        @validate_arguments
        def sum_values(values: list[int]) -> int:
            return sum(values)

        self.assertEqual(sum_values([2, 3]), 5)
        with self.assertRaises(RuntimeValidationError):
            sum_values([2, "bad"])  # type: ignore[list-item]

    def test_trace_decorator_tracks_calls_depth_and_redacts_inputs(
        self,
    ) -> None:
        registry = TraceRegistry()

        @trace_calls(registry=registry)
        def recursive(level: int, password: str = "hidden") -> int:
            if level == 0:
                return 0
            return recursive(level - 1, password=password) + 1

        self.assertEqual(recursive(3, password="do-not-log"), 3)
        summary = registry.summary("recursive")
        events = registry.events("recursive")

        self.assertEqual(summary.call_count, 4)
        self.assertEqual(summary.max_recursion_depth, 4)
        self.assertTrue(
            all("do-not-log" not in str(event.arguments) for event in events)
        )
        self.assertGreaterEqual(summary.total_duration_ms, 0)

    def test_memory_profiler_reports_heap_diff_and_weak_references(
        self,
    ) -> None:
        profiler = MemoryProfiler(top_limit=3)
        profiler.start()
        before = profiler.snapshot("before")
        allocation = bytearray(256_000)
        after = profiler.snapshot("after")

        class SampleObject:
            pass

        sample = SampleObject()
        reference = weakref.ref(sample)
        profiler.track_object(sample, "sample")
        live_objects = profiler.live_objects()
        export = json.loads(profiler.export_json())
        del sample
        gc.collect()
        profiler.stop()

        self.assertGreaterEqual(
            after["current_bytes"], before["current_bytes"]
        )
        self.assertTrue(after["top_allocations"])
        self.assertEqual(live_objects[0]["label"], "sample")
        self.assertIsNone(reference())
        self.assertEqual(export["snapshot_count"], 2)
        self.assertGreater(len(allocation), 0)

    def test_circuit_breaker_trips_and_recovers_after_cooldown(self) -> None:
        now = [100.0]
        breaker = CircuitBreaker(
            name="test-downstream",
            failure_threshold=2,
            recovery_timeout=5.0,
            clock=lambda: now[0],
        )

        def fail() -> None:
            raise OSError("downstream unavailable")

        with self.assertRaises(OSError):
            breaker.call(fail)
        with self.assertRaises(OSError):
            breaker.call(fail)
        self.assertEqual(breaker.state, CircuitState.OPEN)
        with self.assertRaises(CircuitOpenError):
            breaker.call(lambda: "blocked")

        now[0] += 6
        self.assertEqual(breaker.call(lambda: "recovered"), "recovered")
        self.assertEqual(breaker.state, CircuitState.CLOSED)

    def test_circuit_breaker_reopens_after_failed_half_open_probe(
        self,
    ) -> None:
        now = [0.0]
        breaker = CircuitBreaker(
            name="probe-failure",
            failure_threshold=1,
            recovery_timeout=2.0,
            clock=lambda: now[0],
        )
        with self.assertRaises(OSError):
            breaker.call(lambda: (_ for _ in ()).throw(OSError("failed")))
        now[0] += 3
        with self.assertRaises(OSError):
            breaker.call(
                lambda: (_ for _ in ()).throw(OSError("failed again"))
            )
        self.assertEqual(breaker.state, CircuitState.OPEN)

    def test_thread_safe_store_copies_values_and_serializes_updates(
        self,
    ) -> None:
        store = ThreadSafeStateStore()
        store.set("profile", {"roles": ["reader"]})
        returned = store.get("profile")
        returned["roles"].append("admin")
        self.assertEqual(store.get("profile"), {"roles": ["reader"]})

        store.set("counter", 0)
        workers = [
            threading.Thread(
                target=lambda: [
                    store.update("counter", lambda value: value + 1)
                    for _ in range(100)
                ]
            )
            for _ in range(8)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(store.get("counter"), 800)

    def test_thread_safe_store_rejects_uncopyable_state(self) -> None:
        store = ThreadSafeStateStore()
        with self.assertRaises(StateCorruptionError):
            store.set("lock", threading.Lock())

    def test_reliability_metrics_compute_mttr_frequency_and_catch_ratio(
        self,
    ) -> None:
        now = [100.0]
        metrics = ReliabilityMetrics(clock=lambda: now[0])
        metrics.record_fault()
        metrics.record_caught()
        metrics.record_recovery(4.0)
        now[0] += 60
        metrics.record_fault()

        summary = metrics.snapshot()
        self.assertEqual(summary["fault_count"], 2)
        self.assertEqual(summary["caught_count"], 1)
        self.assertEqual(summary["mttr_seconds"], 4.0)
        self.assertEqual(summary["fault_frequency_per_minute"], 2.0)
        self.assertEqual(summary["catch_ratio"], 0.5)

    def test_synthetic_debug_session_steps_without_evaluating_user_code(
        self,
    ) -> None:
        session = create_debug_session("division_by_zero")
        steps = []
        while not session.done:
            steps.append(session.step())

        self.assertTrue(steps)
        report = session.report
        self.assertEqual(report["scenario"], "division_by_zero")
        self.assertEqual(report["exception_type"], "MathDomainFault")
        self.assertTrue(report["frames"])
        with self.assertRaises(ValueError):
            create_debug_session("arbitrary code")


if __name__ == "__main__":
    unittest.main()
