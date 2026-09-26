"""
Automated Test Suite for Inventory Analyzer Bug Remediation
Author: Akshya Gupta
Covers regression checks for Bugs 1 through 5.
"""

import unittest
from inventory_analyzer_fixed import InventoryItem, InventoryPipeline

class TestInventoryPipelineHardening(unittest.TestCase):

    def test_bug1_mutable_default_isolation(self):
        """Verify distinct history lists across independent parse executions."""
        item1 = InventoryItem.from_raw("1", "Item A", 10, [5.0])
        item2 = InventoryItem.from_raw("2", "Item B", 20, [10.0])
        self.assertEqual(item1.history, [10])
        self.assertEqual(item2.history, [20])
        self.assertIsNot(item1.history, item2.history)

    def test_bug2_empty_price_zero_division(self):
        """Verify ZeroDivisionError is avoided when prices list is empty."""
        item = InventoryItem.from_raw("3", "Zero Price", 15, [])
        self.assertEqual(item.avg_price, 0.0)

    def test_bug3_missing_file_handling(self):
        """Verify missing source files raise controlled FileNotFoundError."""
        with self.assertRaises(FileNotFoundError):
            InventoryPipeline.load_dataset("non_existent_data_file.json")

    def test_bug4_negative_stock_infinite_loop_prevention(self):
        """Verify negative stock items are filtered without stalling execution."""
        mock_items = [
            InventoryItem.from_raw("A", "Valid", 20, [10.0]),
            InventoryItem.from_raw("B", "Corrupt Negative", -99, [5.0]),
            InventoryItem.from_raw("C", "Low Stock", 5, [2.0])
        ]
        filtered = InventoryPipeline.filter_batches(mock_items, min_stock=10)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].id, "A")

    def test_bug5_safe_valuation_aggregation(self):
        """Verify calculation runs accurately without TypeError/KeyError."""
        mock_items = [
            InventoryItem.from_raw("A", "Item 1", 10, [100.0]),
            InventoryItem.from_raw("B", "Item 2", 5, [50.0])
        ]
        total = InventoryPipeline.calculate_valuation(mock_items)
        self.assertEqual(total, 1250.0)

if __name__ == "__main__":
    unittest.main()
