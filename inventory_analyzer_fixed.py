"""
Enterprise Inventory & Valuation Pipeline - Production Hardened
Author: Akshya Gupta (YuvaIntern Junior Python Developer Track - Week 2)
Engineered for fault-tolerant I/O, strict schema validation, and defensive calculation.
"""

from __future__ import annotations
import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)d) %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("InventoryProcessor")

class InventoryProcessingError(Exception):
    """Base domain exception for inventory parsing and batch failures."""

@dataclass
class InventoryItem:
    id: str
    name: str
    stock: int
    avg_price: float
    history: List[int] = field(default_factory=list)

    @classmethod
    def from_raw(cls, item_id: Any, name: Any, stock: Any, prices: Any, history: Optional[List[int]] = None) -> InventoryItem:
        clean_id = str(item_id).strip() if item_id is not None else "UNKNOWN_SKU"
        clean_name = str(name).strip() if name is not None else "Unnamed Item"
        
        try:
            clean_stock = int(stock)
        except (ValueError, TypeError):
            logger.warning("Coercion failure for stock '%s' on item %s; fallback to 0.", stock, clean_id)
            clean_stock = 0

        # Fix Bug 1: State contamination isolated via factory copy
        local_history = list(history) if history is not None else []
        local_history.append(clean_stock)

        # Fix Bug 2: Defensive ZeroDivisionError prevention
        clean_prices: List[float] = []
        if isinstance(prices, (list, tuple)):
            for p in prices:
                try:
                    p_float = float(p)
                    if p_float >= 0:
                        clean_prices.append(p_float)
                except (ValueError, TypeError):
                    continue

        avg_price = round(sum(clean_prices) / len(clean_prices), 2) if clean_prices else 0.0

        return cls(
            id=clean_id,
            name=clean_name,
            stock=clean_stock,
            avg_price=avg_price,
            history=local_history
        )

class InventoryPipeline:
    @staticmethod
    def load_dataset(filepath: str) -> List[Dict[str, Any]]:
        """Fix Bug 3: Safe context management & explicit error trapping."""
        if not os.path.exists(filepath):
            logger.error("Dataset not found at '%s'", filepath)
            raise FileNotFoundError(f"Missing required data file: {filepath}")

        try:
            with open(filepath, 'r', encoding='utf-8') as stream:
                data = json.load(stream)
                if not isinstance(data, list):
                    logger.error("Expected list schema, received %s", type(data).__name__)
                    return []
                return data
        except json.JSONDecodeError as err:
            logger.critical("Syntax corruption encountered in '%s': %s", filepath, err)
            raise InventoryProcessingError(f"Corrupted JSON payload: {err}") from err

    @staticmethod
    def filter_batches(items: List[InventoryItem], min_stock: int = 10) -> List[InventoryItem]:
        """Fix Bug 4: Generator-based processing eliminating infinite pointer traps."""
        valid_items: List[InventoryItem] = []
        for item in items:
            if item.stock < 0:
                logger.debug("Discarding anomalous negative stock record: SKU=%s", item.id)
                continue
            if item.stock >= min_stock:
                valid_items.append(item)
        return valid_items

    @staticmethod
    def calculate_valuation(items: List[InventoryItem]) -> float:
        """Fix Bug 5: Type-safe arithmetic valuation with zero KeyError vulnerability."""
        total = 0.0
        for item in items:
            total += item.stock * item.avg_price
        return round(total, 2)

def main() -> None:
    parser = argparse.ArgumentParser(description="Advanced Python Inventory Analyzer Pipeline")
    parser.add_argument("--source", default="sample_inventory.json", help="Path to input JSON dataset")
    parser.add_argument("--threshold", type=int, default=10, help="Minimum stock threshold for reporting")
    args = parser.parse_args()

    if not os.path.exists(args.source):
        fixtures = [
            {"id": "SKU-PRO-01", "name": "Mechanical Keyboard", "stock": 25, "prices": [120.0, 115.5, 118.0]},
            {"id": "SKU-PRO-02", "name": "USB-C Braided Cable", "stock": -5, "prices": [12.0, 10.0]},
            {"id": "SKU-PRO-03", "name": "Gaming Optical Mouse", "stock": 14, "prices": []},
            {"id": "SKU-PRO-04", "name": "4K IPS Monitor", "stock": 8, "prices": [300.0, 295.0]},
            {"id": "SKU-PRO-05", "name": "Thunderbolt 4 Dock", "stock": 12, "prices": [199.99, 189.50]}
        ]
        with open(args.source, 'w', encoding='utf-8') as f:
            json.dump(fixtures, f, indent=2)
        logger.info("Initialized default verification fixture: %s", args.source)

    try:
        raw_data = InventoryPipeline.load_dataset(args.source)
        items = [InventoryItem.from_raw(r.get('id'), r.get('name'), r.get('stock'), r.get('prices')) for r in raw_data]
        stocked_items = InventoryPipeline.filter_batches(items, min_stock=args.threshold)
        total_valuation = InventoryPipeline.calculate_valuation(stocked_items)

        print("\n=======================================================")
        print("     ENTERPRISE INVENTORY PIPELINE AUDIT REPORT        ")
        print("=======================================================")
        print(f"Total Transactions Ingested:    {len(items)}")
        print(f"Eligible Stock Units (>={args.threshold}):      {len(stocked_items)}")
        print(f"Aggregate Market Capitalization: ${total_valuation:,.2f}")
        print("=======================================================\n")
    except Exception as exc:
        logger.exception("Pipeline terminated unexpectedly: %s", exc)
        sys.exit(1)

if __name__ == "__main__":
    main()
