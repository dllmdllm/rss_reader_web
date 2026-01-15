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

def parse_items(payload: bytes | str, source: str, category: str = "") -> list[Item]:
    items: list[Item] = []
    if isinstance(payload, bytes):
        text = payload.decode("utf-8", errors="ignore")
    else:
        text = payload
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
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

            for item in root.xpath("//item"):
                title = lxml_text(item, "title")
                link = lxml_text(item, "link")
                link = normalize_link(link)
                pub_text = lxml_text(item, "pubDate")
                pub_dt = parse_pub_date(pub_text)
                desc_raw = lxml_text(item, "encoded") or lxml_text(item, "description")
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
