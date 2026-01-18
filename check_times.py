import json
import re
from datetime import datetime

with open("index.html", "r", encoding="utf-8") as f:
    text = f.read()

# Extract build time
m_build = re.search(r'Updated:\s*(\d{2}:\d{2})', text)
print(f"Build Time: {m_build.group(1) if m_build else 'Unknown'}")

# Extract news timestamps
times = re.findall(r'"pub_fmt":\s*"([^"]+)"', text)
if times:
    latest_times = sorted(times, reverse=True)
    print("Latest news timestamps found:")
    for t in latest_times[:10]:
        print(f"  - {t}")
else:
    print("No news timestamps found in index.html")
