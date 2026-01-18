
from rss_core.utils import strip_html

def is_blocked_item(title: str, url: str) -> bool:
    clean_title = strip_html(title).strip()
    
    # 1. URL Filters
    if '/zone/' in url or '/campaign/' in url: return True
    
    # 2. Title Keyword Filters
    block_keywords = [
        "如何隱藏", "App內廣告", "刪除會員帳戶", 
        "會員資訊", "尊享會員優惠", "會員優惠不斷更新",
        "如果你想解決", "低成本啟動方法", "轉數快 2024"
    ]
    if any(k in clean_title for k in block_keywords): return True
    if clean_title == "會員資訊": return True
    
    return False

# Test Case
title_to_test = "如何隱藏《香港01》App內廣告？即睇低成本啟動方法！"
url_to_test = "https://www.hk01.com/article/12345"

is_blocked = is_blocked_item(title_to_test, url_to_test)
print(f"Title: {title_to_test}")
print(f"Is Blocked: {is_blocked}")
