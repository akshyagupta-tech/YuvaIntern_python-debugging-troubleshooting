"""Demonstrations of shared state bugs and defensive Python patterns."""

import math
from pathlib import Path
from typing import List, Optional, Sequence, Union


def append_record(data: str, store: List[str] = []) -> List[str]:
    """Demonstrate how a mutable default is shared between calls."""
    store.append(data)
    return store


def append_record_safe(
    data: str,
    store: Optional[List[str]] = None,
) -> List[str]:
    """Append data without sharing a default list across calls."""
    if store is None:
        store = []
    store.append(data)
    return store


class RecordCollection:
    """Store records in a list unique to this collection instance."""

    def __init__(self) -> None:
        self.records: List[str] = []

    def add(self, data: str) -> None:
        append_record_safe(data, self.records)


class SafeNumericAnalyzer:
    """Calculate an average after validating the input values."""

    @staticmethod
    def average(values: Sequence[object]) -> float:
        if not values:
            raise ValueError(
                "Cannot calculate an average from an empty dataset."
            )

        numeric_values: List[Union[int, float]] = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError("Dataset values must be numeric.")
            if math.isnan(value):
                raise ValueError("Dataset values must not contain NaN.")
            numeric_values.append(value)

        return sum(numeric_values) / len(numeric_values)


def read_text(path: Union[str, Path]) -> str:
    """Read a UTF-8 text file and close it even if reading fails."""
    with Path(path).open("r", encoding="utf-8") as file:
        return file.read()


def _run_streamlit_app() -> None:
    """Render an interactive comparison of shared and isolated state."""
    import streamlit as st

    if "week2_fixed_records" not in st.session_state:
        st.session_state.week2_fixed_records = RecordCollection()
    if "week2_buggy_records" not in st.session_state:
        st.session_state.week2_buggy_records = []

    st.title("Mutable Default Argument Lab")
    record = st.text_input("Record to append", value="sample")
    buggy_column, fixed_column = st.columns(2)

    with buggy_column:
        st.subheader("Buggy: shared default")
        if st.button("Append to shared list", key="week2_buggy_append"):
            append_record(record)
        buggy_store = append_record.__defaults__[0]
        st.write("Records:", buggy_store)
        st.caption(f"Shared list memory ID: {id(buggy_store)}")

    with fixed_column:
        st.subheader("Fixed: instance-owned list")
        fixed_records: RecordCollection = st.session_state.week2_fixed_records
        if st.button("Append to this instance", key="week2_fixed_append"):
            fixed_records.add(record)
        st.write("Records:", fixed_records.records)
        st.caption(f"Instance list memory ID: {id(fixed_records.records)}")


if __name__ == "__main__":
    _run_streamlit_app()