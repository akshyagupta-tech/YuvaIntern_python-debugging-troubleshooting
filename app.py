"""Interactive defensive engineering laboratory."""

from __future__ import annotations

import json
from typing import Any, Dict

import streamlit as st

from diagnostics import (
    DiagnosticLogger,
    TraceRegistry,
    build_exception_report,
    trace_calls,
)
from profiler import MemoryProfiler
from reliability_core import (
    CircuitBreaker,
    ReliabilityMetrics,
    SafeArithmetic,
    ThreadSafeStateStore,
    append_record_safe,
    create_debug_session,
)

st.set_page_config(
    page_title="Enterprise Python Reliability Lab",
    layout="wide",
)

TRACE_REGISTRY = TraceRegistry()


@trace_calls(registry=TRACE_REGISTRY)
def _traced_add(left: float, right: float) -> float:
    return SafeArithmetic.sanitize(left + right)


def _initialize_session() -> None:
    if "reliability_state" in st.session_state:
        return
    st.session_state.reliability_state = ThreadSafeStateStore()
    st.session_state.reliability_metrics = ReliabilityMetrics()
    st.session_state.memory_profiler = MemoryProfiler(top_limit=8)
    st.session_state.diagnostic_logger = DiagnosticLogger(
        name=f"enterprise.lab.session.{id(st.session_state)}"
    )
    st.session_state.circuit_breaker = CircuitBreaker(
        "synthetic-downstream",
        failure_threshold=3,
        recovery_timeout=15.0,
    )
    st.session_state.reliability_state.set(
        "buggy_records",
        [],
    )
    st.session_state.reliability_state.set(
        "fixed_runs",
        [],
    )
    st.session_state.trace_report = None
    st.session_state.debug_session = None
    st.session_state.heap_payload = None


def _log_fault(message: str, **context: object) -> None:
    metrics: ReliabilityMetrics = st.session_state.reliability_metrics
    logger: DiagnosticLogger = st.session_state.diagnostic_logger
    metrics.record_fault()
    metrics.record_caught()
    logger.error(message, **context)


def _export_snapshot() -> str:
    state: ThreadSafeStateStore = st.session_state.reliability_state
    metrics: ReliabilityMetrics = st.session_state.reliability_metrics
    profiler: MemoryProfiler = st.session_state.memory_profiler
    logger: DiagnosticLogger = st.session_state.diagnostic_logger
    payload: Dict[str, Any] = {
        "state": state.snapshot(),
        "reliability_metrics": metrics.snapshot(),
        "trace_events": [event.__dict__ for event in TRACE_REGISTRY.events()],
        "heap": json.loads(profiler.export_json()),
        "logs": [
            json.loads(line)
            for line in logger.json_output.splitlines()
            if line.strip()
        ],
        "traceback": st.session_state.trace_report,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _render_sandbox() -> None:
    st.subheader("State and arithmetic fault injection")
    mode = st.radio(
        "Execution mode",
        ("Bug injection", "Defensive fix"),
        horizontal=True,
    )
    payload = st.text_input("Synthetic record", value="diagnostic sample")
    record_column, math_column = st.columns(2)

    with record_column:
        st.markdown("**State isolation**")
        if st.button("Run state scenario", key="run_state_scenario"):
            state: ThreadSafeStateStore = st.session_state.reliability_state
            if mode == "Bug injection":
                records = state.get("buggy_records")
                records.append(payload)
                state.set("buggy_records", records)
                state.set("last_state_result", records)
                _log_fault("Shared state reused across calls", mode=mode)
            else:
                result = append_record_safe(payload)
                state.update(
                    "fixed_runs",
                    lambda runs: [*runs, result],
                    initial=[],
                )
                state.set("last_state_result", result)
                st.session_state.reliability_metrics.record_recovery(0.0)
                st.session_state.diagnostic_logger.info(
                    "Independent state created",
                    mode=mode,
                )
        last_result = st.session_state.reliability_state.get(
            "last_state_result",
            [],
        )
        st.write("Latest records:", last_result)
        st.caption(f"Latest list memory ID: {hex(id(last_result))}")

    with math_column:
        st.markdown("**Numeric boundary**")
        operation = st.selectbox(
            "Operation",
            ("divide", "square root", "average"),
        )
        value_a = st.number_input("Value A", value=12.0)
        value_b = st.number_input("Value B", value=0.0)
        if st.button("Run numeric scenario", key="run_math_scenario"):
            try:
                if mode == "Bug injection":
                    if operation == "divide":
                        result = value_a / value_b
                    elif operation == "square root":
                        result = value_b**0.5
                    else:
                        result = (value_a + value_b) / 0
                elif operation == "divide":
                    result = SafeArithmetic.divide(value_a, value_b)
                elif operation == "square root":
                    result = SafeArithmetic.square_root(value_b)
                else:
                    result = SafeArithmetic.average([value_a, value_b])
            except Exception as error:
                _log_fault(
                    "Numeric scenario caught an exception",
                    mode=mode,
                    operation=operation,
                    error_type=type(error).__name__,
                )
                report = build_exception_report(
                    type(error),
                    error,
                    error.__traceback__,
                )
                st.session_state.trace_report = report
                st.error(f"Caught {type(error).__name__}: {error}")
            else:
                st.session_state.diagnostic_logger.info(
                    "Numeric scenario completed",
                    mode=mode,
                    operation=operation,
                )
                st.success(f"Result: {result}")


def _render_traceback_inspector() -> None:
    st.subheader("Synthetic traceback inspector")
    scenario = st.selectbox(
        "Synthetic edge case",
        ("division_by_zero", "negative_square_root", "non_finite_average"),
    )
    if st.button("Capture traceback", key="capture_traceback"):
        session = create_debug_session(scenario)
        st.session_state.debug_session = session
        st.session_state.trace_report = session.report
        st.session_state.current_frame = None
        st.session_state.diagnostic_logger.warning(
            "Synthetic debug scenario captured",
            scenario=scenario,
            exception_type=session.report["exception_type"],
        )

    session = st.session_state.debug_session
    if session is None:
        st.info(
            "Choose a predefined scenario to inspect its safe frame snapshot."
        )
        return

    st.json(
        {
            key: value
            for key, value in session.report.items()
            if key != "frames"
        }
    )
    if not session.done and st.button("Step to next frame", key="debug_step"):
        st.session_state.current_frame = session.step()
    current_frame = st.session_state.get("current_frame")
    if current_frame is not None:
        st.json(current_frame)
    st.caption(
        f"Frame {min(session._index, len(session._frames))} "
        f"of {len(session._frames)}"
    )
    if st.button("Show full redacted trace", key="show_full_trace"):
        st.json(session.report["frames"])


def _render_telemetry() -> None:
    st.subheader("Execution telemetry")
    trace_column, breaker_column = st.columns(2)
    with trace_column:
        if st.button("Trace a safe computation", key="trace_computation"):
            try:
                _traced_add(4.0, 7.0)
            except Exception as error:
                _log_fault(
                    "Traced computation failed",
                    error_type=type(error).__name__,
                )
        summary = TRACE_REGISTRY.summary("_traced_add")
        st.json(summary.__dict__)
        trace_data = [
            {
                "call": event.call_number,
                "duration_ms": event.duration_ms,
                "depth": event.recursion_depth,
            }
            for event in TRACE_REGISTRY.events("_traced_add")
        ]
        if trace_data:
            st.line_chart(trace_data, x="call", y="duration_ms")

    with breaker_column:
        st.write("Synthetic circuit breaker")
        breaker: CircuitBreaker = st.session_state.circuit_breaker
        st.metric("Breaker state", breaker.state.value)
        if st.button("Trip downstream breaker", key="trip_breaker"):
            try:
                breaker.call(
                    lambda: (_ for _ in ()).throw(
                        OSError("synthetic downstream failure")
                    )
                )
            except Exception as error:
                _log_fault(
                    "Circuit-breaker scenario failed",
                    error_type=type(error).__name__,
                )


def _render_memory_profiler() -> None:
    st.subheader("Heap and weak-reference profiler")
    profiler: MemoryProfiler = st.session_state.memory_profiler
    if st.button("Capture heap sample", key="capture_heap"):
        st.session_state.heap_payload = bytearray(128_000)
        profiler.snapshot("allocated sample")
        st.session_state.diagnostic_logger.info("Heap sample captured")
        st.session_state.heap_payload = None
        profiler.snapshot("after release")
    samples = profiler.heap_series()
    if samples:
        st.line_chart(samples, x="label", y="current_bytes")
        st.dataframe(profiler.live_objects(), width="stretch")
        st.json(json.loads(profiler.export_json()))
    else:
        st.info("Capture a sample to view current and peak heap measurements.")


def main() -> None:
    _initialize_session()
    st.title("Enterprise Python Reliability & Diagnostics Lab")
    metric_container = st.container()
    metrics: ReliabilityMetrics = st.session_state.reliability_metrics
    sandbox_tab, inspector_tab, profiler_tab, audit_tab = st.tabs(
        (
            "Bug lab",
            "Traceback inspector",
            "Heap profiler",
            "Audit & telemetry",
        )
    )
    with sandbox_tab:
        _render_sandbox()
    with inspector_tab:
        _render_traceback_inspector()
    with profiler_tab:
        _render_memory_profiler()
    with audit_tab:
        _render_telemetry()
        logger: DiagnosticLogger = st.session_state.diagnostic_logger
        st.subheader("Recent structured fault logs")
        log_lines = logger.json_output.splitlines()
        st.code(
            "\n".join(log_lines[-30:]) or "No events yet.", language="json"
        )
        st.download_button(
            "Download diagnostic snapshot",
            data=_export_snapshot(),
            file_name="reliability-diagnostics.json",
            mime="application/json",
        )
        st.download_button(
            "Download structured log",
            data="\n".join(logger.json_output.splitlines()),
            file_name="reliability-events.jsonl",
            mime="application/x-ndjson",
        )

    summary = metrics.snapshot()
    with metric_container:
        metric_columns = st.columns(4)
        metric_columns[0].metric("Faults", summary["fault_count"])
        metric_columns[1].metric("MTTR", f"{summary['mttr_seconds']:.2f} s")
        metric_columns[2].metric(
            "Faults / min",
            f"{summary['fault_frequency_per_minute']:.2f}",
        )
        metric_columns[3].metric(
            "Caught ratio",
            f"{summary['catch_ratio']:.0%}",
        )


if __name__ == "__main__":
    main()
