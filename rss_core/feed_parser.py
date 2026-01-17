import re
import xml.etree.ElementTree as ET
from typing import Any, Optional
from datetime import datetime
import html

try:
    from lxml import etree as LXML_ETREE
except ImportError:
    LXML_ETREE = None

from .model import Item
from .utils import strip_html, normalize_link, normalize_image_url, parse_pub_date, is_generic_image
from .parser import to_trad

def find_text(node: ET.Element, tag: str) -> str:
    child = node.find(tag)
    if child is None:
        return ""
    return (child.text or "").strip()


def find_text_any(node: ET.Element, tags: list[str]) -> str:
    for tag in tags:
        child = node.find(tag)
        if child is not None and (child.text or "").strip():
            return (child.text or "").strip()
    child = node.find("{http://purl.org/rss/1.0/modules/content/}encoded")
    if child is not None and (child.text or "").strip():
        return (child.text or "").strip()
    return ""

def get_rss_image(item: ET.Element, base_url: str) -> str:
    for child in item:
        tag = child.tag.lower()
        if tag.endswith("enclosure"):
            url = normalize_image_url(base_url, child.attrib.get("url", "") or "")
            return "" if is_generic_image(url, base_url) else url
        if "media" in tag and tag.endswith("content"):
            url = normalize_image_url(base_url, child.attrib.get("url", "") or "")
            return "" if is_generic_image(url, base_url) else url
    desc = find_text(item, "description") or ""
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc)
    if match:
        url = normalize_image_url(base_url, match.group(1))
        return "" if is_generic_image(url, base_url) else url
    match = re.search(r'<img[^>]+data-src=["\']([^"\']+)["\']', desc)
    if match:
        url = normalize_image_url(base_url, match.group(1))
        return "" if is_generic_image(url, base_url) else url
    return ""


def get_rss_image_from_desc(desc: str, base_url: str) -> str:
    if not desc:
        return ""
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc)
    if match:
        url = normalize_image_url(base_url, match.group(1))
        return "" if is_generic_image(url, base_url) else url
    match = re.search(r'<img[^>]+data-src=["\']([^"\']+)["\']', desc)
    if match:
        url = normalize_image_url(base_url, match.group(1))
        return "" if is_generic_image(url, base_url) else url
    return ""

def to_trad_if_cnbeta(source: str, text: str) -> str:
    if not text:
        return text
    if "cnbeta" in (source or ""):
        return to_trad(text)
    return text

def scrape_html_feed(text: str, source: str) -> list[Item]:
    """Fallback scraper for HTML-only 'feeds' (e.g. HK01, On.cc index pages)."""
    items = []
    base_url = ""
    if "hk01" in source.lower():
        base_url = "https://www.hk01.com"
    elif "on.cc" in source.lower():
        base_url = "https://hk.on.cc"
        
    try:
        if LXML_ETREE is None:
             # Basic regex fallback if lxml missing (unlikely in this env)
             return []
             
        # Use lxml.html for lenient parsing
        from lxml import html
        doc = html.fromstring(text)
        doc.make_links_absolute(base_url)
        
        # HK01 Scraper
        if "hk01" in source.lower():
            import json
            # Try JSON first (Next.js data)
            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', text, re.S)
            if match:
                print("HK01 '__NEXT_DATA__' script block found.")
                try:
                    data = json.loads(match.group(1))
                    print(f"JSON Keys: {list(data.keys())}")
                    
                    def find_articles(obj):
                        if isinstance(obj, dict):
                            # Aggressive search for article-like objects
                            has_title = 'title' in obj and isinstance(obj['title'], str) and len(obj['title']) > 3
                            
                            # Check for URL keys first
                            url = obj.get('publishUrl') or obj.get('url') or obj.get('originalUrl')
                            
                            # Check nested data.url
                            if not url and 'data' in obj and isinstance(obj['data'], dict):
                                d = obj['data']
                                url = d.get('url') or d.get('originalUrl') or d.get('publishUrl')
                                
                                # Lift title if missing
                                if not has_title and 'title' in d and isinstance(d['title'], str) and len(d['title']) > 3:
                                    obj['title'] = d['title']
                                    has_title = True
                                
                                # Lift publishTime if missing
                                if 'publishTime' not in obj and 'publishTime' in d:
                                    obj['publishTime'] = d['publishTime']

                            # Robust check: accepts /article/ OR presence of articleId
                            is_article_url = url and isinstance(url, str) and ('/article/' in url or re.search(r'/\d+/', url))
                            has_id = 'articleId' in obj or ('data' in obj and isinstance(obj['data'], dict) and 'articleId' in obj['data'])

                            if has_title and (is_article_url or has_id):
                                obj['scraped_url'] = url
                                yield obj
                            elif has_title:
                                # Maybe URL is in a different key?
                                # Check all values for article path
                                for v in obj.values():
                                    if isinstance(v, str) and '/article/' in v and v.startswith('/'):
                                        # Found a potential relative link, inject it as 'scraped_url'
                                        obj['scraped_url'] = v
                                        yield obj
                                        break

                            for k, v in obj.items():
                                yield from find_articles(v)
                                
                        elif isinstance(obj, list):
                            for item in obj:
                                yield from find_articles(item)

                    seen = set()
                    for art in find_articles(data):
                        url_path = art.get('publishUrl') or art.get('url') or art.get('originalUrl') or art.get('scraped_url')
                        title = art.get('title')
                        
                        if url_path and title:
                             # Ensure text title
                             if not isinstance(title, str): continue
                             
                             if url_path in seen: continue
                             seen.add(url_path)
                             
                             full_url = url_path
                             if not full_url.startswith('http'):
                                 # HK01 absolute links often lack protocol or are relative
                                 full_url = base_url + url_path if url_path.startswith('/') else base_url + '/' + url_path
                             
                             if '/article/' not in full_url and not re.search(r'/\d+/', full_url): continue

                             # Fix timestamp
                             dt = datetime.now()
                             if 'publishTime' in art:
                                 try:
                                     dt = datetime.fromtimestamp(int(art['publishTime']))
                                 except: pass

                             items.append(Item(
                                title=strip_html(title),
                                link=full_url,
                                pub_dt=dt,
                                pub_text="",
                                source=source,
                                rss_image="",
                                category="",
                                summary=""
                             ))
                    
                    if items:
                        print(f"HK01 JSON Scraper found {len(items)} items.")
                        return items
                        
                except Exception as e:
                     print(f"HK01 JSON Scrape Error: {e}")

            # HTML Fallback
            nodes = doc.xpath("//a[contains(@href, '/article/')]")
            print(f"HK01 HTML Scraper found {len(nodes)} candidate nodes.")
            seen = set()
            for a in nodes:
                href = a.get("href")
                if not href or href in seen: continue
                title = "".join(a.xpath(".//text()")).strip()
                if not title or len(title) < 5: continue
                
                seen.add(href)
                
                full_url = href
                if not full_url.startswith('http'):
                     full_url = base_url + href if href.startswith('/') else base_url + '/' + href

                items.append(Item(
                    title=strip_html(title),
                    link=full_url,
                    pub_dt=datetime.now(),
                    pub_text="",
                    source=source,
                    rss_image="",
                    category="",
                    summary=""
                ))
            
            # Regex Fallback (Last Resort)
            if not items:
                print("HK01: Triggering Regex Fallback")
                # Robust regex for /article/ links
                # Just find the paths directly as seen in debug script
                pat = r'(/article/\d+)'
                raw_links = set(re.findall(pat, text))
                print(f"HK01 Regex Found {len(raw_links)} links.")
                
                for rlink in raw_links:
                     if rlink in seen: continue
                     seen.add(rlink)
                     
                     full_url = rlink
                     if not full_url.startswith('http'):
                         full_url = base_url + rlink if rlink.startswith('/') else base_url + '/' + rlink
                         
                     title = "HK01 Article"
                     # Try to find title
                     try:
                         # Look for href="...rlink..." > ... <
                         # We accept partial match on rlink if needed but rlink is path so it should be in href
                         t_match = re.search(f'href=["\'][^"\']*?{re.escape(rlink)}[^"\']*?["\'][^>]*>(.*?)</a>', text, re.S)
                         if t_match:
                             candidate = strip_html(t_match.group(1)).strip()
                             if len(candidate) > 5:
                                 title = candidate
                     except: pass
                     
                     items.append(Item(
                        title=title,
                        link=full_url,
                        pub_dt=datetime.now(),
                        pub_text="",
                        source=source,
                        rss_image="",
                        category="",
                        summary=""
                     ))

        # On.cc Scraper
        elif "on.cc" in source.lower():
            # On.cc links: /hk/news/2026/01/16/....html
            # Often in <div class="focusItem"> or just lists
            seen = set()
            # Regex for standard news link pattern: /bkn/cnt/... or /hk/news/... 
            # Actually on.cc uses: https://hk.on.cc/hk/bkn/cnt/news/20250116/bkn-202501160000_00822_001.html
            for a in doc.xpath("//a"):
                href = a.get("href")
                if not href: continue
                
                # Check pattern
                if "/bkn/cnt/" in href or "/news/" in href and href.endswith(".html"):
                    if "index.html" in href: continue
                    
                    if href in seen: continue
                    seen.add(href)
                    
                    title = a.get("title") or "".join(a.xpath(".//text()")).strip()
                    if not title or len(title) < 5: continue
                    
                    items.append(Item(
                        title=strip_html(title),
                        link=href,
                        pub_dt=datetime.now(),
                        pub_text="",
                        source=source,
                        rss_image="",
                        category="",
                        summary=""
                    ))
                    
    except Exception as e:
        print(f"Scrape Error {source}: {e}")
        
    return items

def parse_items(payload: bytes | str, source: str, category: str = "") -> list[Item]:
    items: list[Item] = []
    if isinstance(payload, bytes):
        text = payload.decode("utf-8", errors="ignore")
    else:
        text = payload
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    
    # Detect HTML vs XML
    # Simple check: <rss, <feed, <?xml vs <!DOCTYPE html, <html
    if "<!DOCTYPE html" in text[:500] or "<html" in text[:500]:
        # Fallback to scraper
        return scrape_html_feed(text, source)

    try:
        root = ET.fromstring(text)
        for item in root.findall(".//item"):
            title = find_text(item, "title")
            link = find_text(item, "link")
            link = normalize_link(link)
            pub_text = find_text(item, "pubDate")
            pub_dt = parse_pub_date(pub_text)
            summary = find_text_any(item, ["encoded", "description"])
            rss_image = get_rss_image(item, link)
            items.append(
                Item(
                    title=to_trad_if_cnbeta(source, strip_html(title)),
                    link=link,
                    pub_dt=pub_dt,
                    pub_text=pub_text,
                    source=source,
                    category=category,
                    summary=to_trad_if_cnbeta(source, strip_html(summary)),
                    rss_image=rss_image,
                )
            )
        return items
    except ET.ParseError:
        try:
            if LXML_ETREE is None:
                raise Exception("lxml not available")
            parser = LXML_ETREE.XMLParser(recover=True)
            root = LXML_ETREE.fromstring(text.encode("utf-8"), parser=parser)

            def lxml_text(node: Any, tag: str) -> str:
                child = node.find(tag)
                if child is not None and (child.text or "").strip():
                    return (child.text or "").strip()
                if tag == "encoded":
                    try:
                        child = node.find("{http://purl.org/rss/1.0/modules/content/}encoded")
                        if child is not None and (child.text or "").strip():
                            return (child.text or "").strip()
                    except Exception:
                        pass
                    try:
                        found = node.xpath(".//*[local-name()='encoded']")
                        if found:
                            text = (found[0].text or "").strip()
                            if text:
                                return text
                    except Exception:
                        pass
                return ""
            
            # Check for generic RSS/Atom via xpath
            # If standard Atom/RSS
            rss_items = root.xpath("//item")
            if not rss_items:
                 rss_items = root.xpath("//entry") # Atom
            
            if not rss_items:
                # If XML parsed but no items found, and it looked like HTML earlier...
                # Actually we handled HTML detection at top.
                # Maybe it is just broken XML.
                pass

            for item in rss_items:
                title = lxml_text(item, "title")
                link = lxml_text(item, "link") or lxml_text(item, "id") or "" 
                # Atom 'link' is often an attribute href
                if not link and item.xpath("./link/@href"):
                     link = item.xpath("./link/@href")[0]

                link = normalize_link(link)
                pub_text = lxml_text(item, "pubDate") or lxml_text(item, "published") or lxml_text(item, "updated")
                pub_dt = parse_pub_date(pub_text)
                desc_raw = lxml_text(item, "encoded") or lxml_text(item, "description") or lxml_text(item, "content") or lxml_text(item, "summary")
                summary = desc_raw
                rss_image = get_rss_image_from_desc(desc_raw, link)
                items.append(
                    Item(
                        title=to_trad_if_cnbeta(source, strip_html(title)),
                        link=link,
                        pub_dt=pub_dt,
                        pub_text=pub_text,
                        source=source,
                        category=category,
                        summary=to_trad_if_cnbeta(source, strip_html(summary)),
                        rss_image=rss_image,
                    )
                )
            return items
        except Exception:
            # Maybe raise or log
            print(f"Error parsing feed: {source}")
            return []
