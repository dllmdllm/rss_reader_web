import json
import re

try:
    with open("index.html", "r", encoding="utf-8") as f:
        text = f.read()
    
    # Simple regex to find source counts
    sources = re.findall(r'"source":\s*"([^"]+)"', text)
    from collections import Counter
    print("Found Sources:", Counter(sources))
    print("Unique Sources:", sorted(list(set(sources))))
        
except Exception as e:
    print(f"Error: {e}")
