# Enterprise Python Reliability & Diagnostics Lab

A defensive engineering lab for deterministic resource cleanup, safe
traceback inspection, structured diagnostics, fault tolerance, and heap
telemetry.

## Architecture

- `diagnostics.py`: Reliability exceptions, JSON/syslog logging, redacted
	exception reports, runtime validators, and sync/async trace decorators.
- `reliability_core.py`: Sentinel-based state isolation, guarded arithmetic,
	managed file/socket contexts, thread-safe state, circuit breaker, MTTR/fault
	metrics, and allow-listed synthetic debug sessions.
- `profiler.py`: `tracemalloc` allocation deltas and weak-reference lifetime
	tracking without retaining inspected objects.
- `app.py`: Streamlit bug/fix controls, redacted frame inspector, heap graph,
	fault log, reliability metrics, and diagnostic downloads.
- `test_reliability.py`: Deterministic regression tests for the reliability
	components.
- `inventory_analyzer_fixed.py` and `test_inventory_analyzer.py`: Original
	Week 2 inventory exercises and regression suite.

The traceback sandbox only runs fixed, synthetic scenarios. It does not accept
or evaluate Python source, and exception locals are redacted before display or
export.

## Run

Install the Streamlit dependency and launch the dashboard:

```bash
python3 -m pip install -r requirements.txt
streamlit run app.py
```

Run all Week 2 tests:

```bash
python3 -m unittest -v test_reliability.py test_inventory_analyzer.py
python3 -m unittest discover -s tests -p 'test_*.py'
```
