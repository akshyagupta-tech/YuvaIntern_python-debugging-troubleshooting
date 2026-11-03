"""Tests for the Week 2 defensive debugging examples."""

import math
import tempfile
import unittest
from pathlib import Path

from core.week2_debugger import (
    RecordCollection,
    SafeNumericAnalyzer,
    append_record,
    append_record_safe,
    read_text,
)


class TestWeek2Debugger(unittest.TestCase):
    def test_buggy_default_shares_state_between_calls(self) -> None:
        shared_store = append_record.__defaults__[0]
        shared_store.clear()
        try:
            first_result = append_record("first")
            second_result = append_record("second")

            self.assertIs(first_result, second_result)
            self.assertEqual(second_result, ["first", "second"])
        finally:
            shared_store.clear()

    def test_safe_calls_use_isolated_lists(self) -> None:
        first_result = append_record_safe("first")
        second_result = append_record_safe("second")

        self.assertEqual(first_result, ["first"])
        self.assertEqual(second_result, ["second"])
        self.assertIsNot(first_result, second_result)

    def test_record_collections_are_isolated_between_instances(self) -> None:
        first_collection = RecordCollection()
        second_collection = RecordCollection()

        first_collection.add("first")
        second_collection.add("second")

        self.assertEqual(first_collection.records, ["first"])
        self.assertEqual(second_collection.records, ["second"])
        self.assertIsNot(first_collection.records, second_collection.records)

    def test_numeric_analyzer_rejects_invalid_values(self) -> None:
        with self.assertRaises(ValueError):
            SafeNumericAnalyzer.average([])
        with self.assertRaises(ValueError):
            SafeNumericAnalyzer.average([1.0, math.nan])
        with self.assertRaises(TypeError):
            SafeNumericAnalyzer.average([1.0, "invalid"])
        self.assertEqual(SafeNumericAnalyzer.average([2, 4, 6]), 4.0)

    def test_read_text_returns_file_contents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.txt"
            path.write_text("context-managed", encoding="utf-8")

            self.assertEqual(read_text(path), "context-managed")


if __name__ == "__main__":
    unittest.main()