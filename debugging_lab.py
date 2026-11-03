"""Examples of common Python bugs and safer alternatives."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence


def add_item_buggy(item: str, items: list[str] = []) -> list[str]:
    """A mutable default is shared between calls."""
    items.append(item)
    return items


def average_buggy(values: Sequence[float]) -> float:
    """An empty sequence causes ZeroDivisionError."""
    return sum(values) / len(values)


def read_text_buggy(path: str | Path) -> str:
    """The opened file is never explicitly closed."""
    file = open(path, "r", encoding="utf-8")
    return file.read()


@dataclass
class RobustProcessor:
    """Process values and text without shared defaults or leaked files."""

    records: list[str] = field(default_factory=list)

    def add_record(self, record: str) -> None:
        self.records.append(record)

    @staticmethod
    def average(values: Sequence[float]) -> float:
        if not values:
            raise ValueError("Cannot calculate the average of an empty collection.")
        return sum(values) / len(values)

    @staticmethod
    def read_text(path: str | Path) -> str:
        with open(path, "r", encoding="utf-8") as file:
            return file.read()


def main() -> None:
    processor = RobustProcessor()
    processor.add_record("sample")

    print(f"Records: {processor.records}")
    try:
        print(f"Average: {processor.average([2.0, 4.0, 6.0])}")
        processor.average([])
    except ValueError as error:
        print(error)

    sample_path = Path(__file__).with_name("sample_inventory.json")
    print(f"Read {len(processor.read_text(sample_path))} characters safely.")


if __name__ == "__main__":
    main()