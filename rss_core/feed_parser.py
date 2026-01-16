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
            # HK01 usually uses <a href="/article/..."> or data-testid="article-link"
            # We look for links that look like articles
            seen = set()
            for a in doc.xpath("//a[contains(@href, '/article/')]"):
                href = a.get("href")
                if not href or href in seen: continue
                # Title often in a child div or h3
                title = "".join(a.xpath(".//text()")).strip()
                if not title or len(title) < 5: continue
                
                seen.add(href)
                
                # Try to scrape date from parent? Hard. Default to now() or None.
                # Build.py will handle None pub_dt appropriately (or drop if filtering strictly).
                # Wait, build.py filters by cutoff. If None, it skips?
                # "if not item.pub_dt: continue" -> Yes.
                # So we MUST fake a date or try to find it.
                # For "Recent" lists, we can assume "Now" or parse from time ago?
                # Let's use datetime.now() for now as it's "Latest News".
                
                items.append(Item(
                    title=strip_html(title),
                    link=href,
                    pub_dt=datetime.now(), # Approximate for scraped index
                    pub_text="",
                    source=source,
                    rss_image=""
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
                        rss_image=""
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
