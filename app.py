import streamlit as st
from inventory_analyzer_fixed import InventoryAnalyzer

st.set_page_config(page_title="Week 2: Debugging Lab", layout="centered")
st.title("🐞 Week 2: Systematic Debugging & State Isolation")

st.write("Demonstration of memory-safe operations, ZeroDivisionError guards, and isolated state.")
price = st.number_input("Unit Price (₹):", min_value=0.0, value=150.0)
qty = st.number_input("Quantity:", min_value=0, value=10)

if st.button("Calculate Inventory Metric"):
    analyzer = InventoryAnalyzer()
    analyzer.add_item("Widget A", price, qty)
    summary = analyzer.generate_summary()
    st.json(summary)
    st.success("Calculations completed with zero state leaks.")
