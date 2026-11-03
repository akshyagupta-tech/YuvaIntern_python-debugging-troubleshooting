import streamlit as st
from typing import List, Optional, Dict, Any

class InventoryAnalyzer:
    """Hardened Inventory Analyzer demonstrating defensive state isolation."""
    def __init__(self):
        self.items: List[Dict[str, Any]] = []

    def add_item(self, name: str, price: float, quantity: int, tags: Optional[List[str]] = None) -> None:
        # Fix: Immutable default pattern preventing mutable list contamination
        if tags is None:
            tags = []
        self.items.append({
            "name": name,
            "price": float(price),
            "quantity": int(quantity),
            "tags": list(tags)
        })

    def generate_summary(self) -> Dict[str, Any]:
        total_items = sum(item["quantity"] for item in self.items)
        total_value = sum(item["price"] * item["quantity"] for item in self.items)
        
        # Fix: Guard clause preventing ZeroDivisionError
        avg_unit_price = (total_value / total_items) if total_items > 0 else 0.0

        return {
            "unique_skus": len(self.items),
            "total_inventory_count": total_items,
            "total_valuation_inr": round(total_value, 2),
            "average_unit_cost": round(avg_unit_price, 2)
        }

st.set_page_config(page_title="Week 2: Debugging Lab", page_icon="🐞", layout="centered")
st.title("🐞 Week 2: Systematic Debugging & State Isolation")
st.caption("YuvaIntern Junior Python Developer Track • Intern: Akshya Gupta")

st.markdown("""
### Defensive Architecture Highlights:
- **Mutable Default Elimination:** Prevents cross-session list leaks.
- **ZeroDivisionError Guard:** Safe division calculations on empty inventories.
- **Defensive Type Casting:** Robust numeric parsing.
""")

st.divider()

if "analyzer" not in st.session_state:
    st.session_state.analyzer = InventoryAnalyzer()

st.subheader("Interactive Stock Entry")
c1, c2, c3 = st.columns([3, 2, 2])
with c1:
    item_name = st.text_input("Item Name", value="Core Processor i7")
with c2:
    price = st.number_input("Unit Price (₹)", min_value=0.0, value=25000.0, step=500.0)
with c3:
    qty = st.number_input("Quantity", min_value=0, value=5, step=1)

if st.button("➕ Add Inventory Record", use_container_width=True):
    st.session_state.analyzer.add_item(item_name, price, qty)
    st.success(f"Added {qty}x {item_name} safely to inventory state!")

st.divider()

st.subheader("Live Telemetry & Valuation Summary")
summary = st.session_state.analyzer.generate_summary()

m1, m2, m3, m4 = st.columns(4)
m1.metric("Unique SKUs", summary["unique_skus"])
m2.metric("Total Items", summary["total_inventory_count"])
m3.metric("Total Value", f"₹{summary['total_valuation_inr']:,.2f}")
m4.metric("Avg Unit Cost", f"₹{summary['average_unit_cost']:,.2f}")

st.json(summary)

if st.button("🔄 Reset Inventory State"):
    st.session_state.analyzer = InventoryAnalyzer()
    st.rerun()
