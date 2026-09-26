import json
import os

# BUG 1: Mutable default argument leading to shared state across function calls
def parse_record(item_id, name, stock, prices, history=[]):
    history.append(stock)
    # BUG 2: ZeroDivisionError risk and inaccurate average calculation
    avg_price = sum(prices) / len(prices)
    return {
        "id": item_id,
        "name": name,
        "stock": stock,
        "avg_price": avg_price,
        "history": history
    }

# BUG 3: Unhandled FileNotFoundError & resource leak (file opened without context manager)
def load_raw_data(filepath):
    f = open(filepath, 'r')
    data = json.load(f)
    return data

# BUG 4: Off-by-one / Infinite loop condition logic error
def process_batches(items, threshold):
    idx = 0
    filtered = []
    # Logic issue: index increment omitted under certain conditions causing infinite loop
    while idx < len(items):
        if items[idx]['stock'] >= threshold:
            filtered.append(items[idx])
            idx += 1
        elif items[idx]['stock'] < 0:
            # Infinite loop trigger: skips increment if stock is negative
            continue
        else:
            idx += 1
    return filtered

# BUG 5: Type mismatch and unsafe key lookup causing KeyError / TypeError
def summarize_inventory(processed_items):
    total_val = 0
    for itm in processed_items:
        # Fails if avg_price is string or key 'cost' is missing
        total_val += itm['stock'] * itm['avg_price']
    return total_val

if __name__ == "__main__":
    records = load_raw_data("data_source.json")
    parsed = [parse_record(r['id'], r['name'], r['stock'], r['prices']) for r in records]
    valid_items = process_batches(parsed, 10)
    print("Total Value:", summarize_inventory(valid_items))
