import json
import re
from bs4 import BeautifulSoup

def verify():
    with open('index.html', 'r', encoding='utf-8') as f:
        html = f.read()

    # Extract the JSON blob
    match = re.search(r'<script id="newsData" type="application/json">\s*(.*?)\s*</script>', html, re.DOTALL)
    if not match:
        print("Could not find newsData JSON")
        return

    data = json.loads(match.group(1))
    
    count = 0
    for item in data:
        if "cnbeta" in item['source'].lower() or "cnbeta" in item['link']:
            count += 1
            print(f"\n--- Checking CNBeta Item: {item['title']} ---")
            content = item['content']
            
            # Check for duplicate hero image
            # The template uses item['hero_img']. If content starts with this img, it's a dupe.
            hero = item.get('hero_img', '')
            if hero:
                hero_fname = hero.split('/')[-1].split('?')[0].lower()
                print(f"Hero Image: {hero_fname}")
                if hero_fname in content.lower():
                    # Check position
                    idx = content.lower().find(hero_fname)
                    print(f"Hero image filename found in content at index: {idx}")
                    if idx < 200:
                        print("WARNING: Hero image might still be at the start of content!")
                    else:
                        print("Hero image found, but likely later in gallery/content (OK).")
                else:
                    print("Hero image NOT found in content (Clean).")
            
            # Check for garbage icons at the end
            # Look for short tags or weird chars at the end
            soup = BeautifulSoup(content, 'html.parser')
            text = soup.get_text()
            print(f"Tail len: {len(content)}")
            print(f"Last 100 chars text: {text[-100:]!r}")
            print(f"Last 200 chars HTML: {content[-200:]!r}")
            
            # specific checks for unknown chars or empty boxes
            # We are looking for things like  which are often private use area characters
            pua_chars = [c for c in text if 0xE000 <= ord(c) <= 0xF8FF]
            if pua_chars:
                print(f"WARNING: Found Private Use Area characters (likely broken icons): {pua_chars}")
            
            if count >= 3: break

if __name__ == "__main__":
    verify()
