import re
import hashlib
from collections import Counter
try:
    import argostranslate.translate as argos_translate
    ARGOS_AVAILABLE = True
except ImportError:
    argos_translate = None
    ARGOS_AVAILABLE = False
try:
    import stanza
except ImportError:
    stanza = None
try:
    from lxml import html as LXML_HTML
except ImportError:
    LXML_HTML = None

from .parser import to_trad


TRANSLATE_CACHE = {}
STANZA_NLP = None

def translate_en_to_zh(text: str) -> str:
    if not text or not ARGOS_AVAILABLE:
        return text
    if not re.search(r"[A-Za-z]", text):
        return text
    key = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()
    cached = TRANSLATE_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        translated = argos_translate.translate(text, "en", "zh")
        translated = to_trad(translated)
    except Exception:
        translated = text
    TRANSLATE_CACHE[key] = translated
    return translated

def translate_html_fragment_en_to_zh(html_text: str, base_url: str) -> str:
    if not html_text or "<" not in html_text or not ARGOS_AVAILABLE:
        return html_text
    if LXML_HTML is None:
        return html_text
    try:
        root = LXML_HTML.fragment_fromstring(html_text, create_parent="div")
        skip_tags = {"script", "style", "code", "pre"}
        for node in root.iter():
            if node.tag in skip_tags:
                continue
            if node.text and re.search(r"[A-Za-z]", node.text):
                node.text = translate_en_to_zh(node.text)
            if node.tail and re.search(r"[A-Za-z]", node.tail):
                node.tail = translate_en_to_zh(node.tail)
        return "".join(LXML_HTML.tostring(child, encoding="unicode") for child in root)
    except Exception:
        return html_text

def get_stanza_nlp():
    global STANZA_NLP
    if STANZA_NLP is not None:
        return STANZA_NLP or None
    if stanza is None:
        STANZA_NLP = False
        return None
    try:
        STANZA_NLP = stanza.Pipeline(
            "zh",
            processors="tokenize,ner",
            tokenize_no_ssplit=True,
            use_gpu=False,
            verbose=False,
        )
    except Exception:
        STANZA_NLP = False
    return STANZA_NLP or None

# Keyword extraction helpers
surname = set("陳林黃張李王吳劉蔡楊許鄭謝郭姚宋鄧何呂沈王賴曾洪邱廖葉韋潘")
stopwords = set("的了著及與和就於在是有將未可其對於以並及此這個那這些那些因因為所以但是然而而且或者")
org_suffixes = {"局", "署", "處", "會", "院", "廳", "部", "辦", "公司", "集團", "中心", "銀行", "大學", "學校", "協會", "聯會"}
org_regex = re.compile(r"[\u4e00-\u9fff]{2,10}(?:局|署|處|會|院|廳|部|辦|政府|法院|委員會|集團|公司|大學|學校|醫院|銀行|醫管局|天文台|港鐵|機場|法庭|電台|電視台)")
place_regex = re.compile(r"[\u4e00-\u9fff]{2,10}(?:市|區|鎮|縣|省|國|島|灣|海|路|街|道|村|山|河|湖|港)")
money_regex = re.compile(r"(億|萬|千|百|十).*(美元|港元|日圓|人民幣|元)")
money_tail_regex = re.compile(r"(美元|港元|日圓|人民幣|元)$")
digits_regex = re.compile(r"\d")
percent_regex = re.compile(r"\d+(?:%|％)")
unit_regex = re.compile(r"(?:%|％|℃|°c|度)$")
weak_chars = set("的了著及與和就於在是有將未可其對於以並及")
money_units = {"美元", "港元", "人民幣", "元", "圓"}

def valid_token(token: str) -> bool:
    if not token or len(token) < 2:
        return False
    if token in stopwords or token in money_units:
        return False
    if money_regex.search(token):
        return False
    if money_tail_regex.search(token):
        return False
    if percent_regex.search(token):
        return False
    if unit_regex.search(token):
        return False
    if digits_regex.search(token):
        return False
    if any(ch in weak_chars for ch in token) and len(token) <= 2:
        return False
    return True

def extract_keywords(items_texts: list[tuple[str, str]], limit: int = 20) -> list[str]:
    counts: dict[str, float] = {}
    use_stanza = len(items_texts) <= 80
    stanza_nlp = get_stanza_nlp() if use_stanza else None
    
    if stanza_nlp:
        for title, body in items_texts:
            text_blob = f"{title}\n{body}".strip()
            if not text_blob:
                continue
            text_blob = text_blob[:4000]
            try:
                doc = stanza_nlp(text_blob)
            except Exception:
                continue
            for ent in getattr(doc, "ents", []):
                if ent.type not in {"PERSON", "ORG", "GPE", "LOC"}:
                    continue
                token = re.sub(r"\s+", "", ent.text.strip())
                if len(token) < 2 or not valid_token(token):
                    continue
                score = 2.8 if ent.type in {"PERSON", "ORG"} else 2.4
                if token and token in (title or ""):
                    score += 2.0
                counts[token] = counts.get(token, 0.0) + score
        if counts:
            sorted_tokens = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
            return [t for t, _ in sorted_tokens[:limit]]

    # Fallback / heuristic
    phrases = ["財政預算案", "施政報告", "基本法23條", "垃圾徵費"]
    
    def is_person(token: str) -> bool:
        return 2 <= len(token) <= 3 and token[0] in surname and token not in stopwords

    def is_org(token: str) -> bool:
        if any(token.endswith(s) for s in org_suffixes):
            return True
        return bool(org_regex.fullmatch(token))

    def is_place(token: str) -> bool:
        return bool(place_regex.fullmatch(token))

    for title, body in items_texts:
        title_tokens = Counter(re.findall(r"[\u4e00-\u9fff]{2,6}", title or ""))
        body_tokens = Counter(re.findall(r"[\u4e00-\u9fff]{2,6}", body or ""))
        text_blob = f"{title}\n{body}"
        for ph in phrases:
            if ph in text_blob:
                counts[ph] = counts.get(ph, 0.0) + 4.0
        for m in org_regex.finditer(text_blob):
            token = m.group(0)
            if valid_token(token):
                counts[token] = counts.get(token, 0.0) + 2.5
        for m in place_regex.finditer(text_blob):
            token = m.group(0)
            if valid_token(token):
                counts[token] = counts.get(token, 0.0) + 2.0
        for token, freq in title_tokens.items():
            if not valid_token(token):
                continue
            score = 3.5 * min(3, freq)
            if is_org(token): score += 2.2
            elif is_place(token): score += 2.0
            elif is_person(token): score += 2.0
            else: continue
            counts[token] = counts.get(token, 0.0) + score
        for token, freq in body_tokens.items():
            if not valid_token(token):
                continue
            score = 1.2 * min(3, freq)
            if is_org(token): score += 1.8
            elif is_place(token): score += 1.5
            elif is_person(token): score += 1.4
            else: continue
            counts[token] = counts.get(token, 0.0) + score
    
    sorted_tokens = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return [t for t, _ in sorted_tokens[:limit]]

def count_keywords_in_texts(items_texts: list[tuple[str, str]], keywords: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {k: 0 for k in keywords}
    if not keywords:
        return counts
    synonym_map = {
        "特區政府": "政府",
        "港府": "政府",
        "政府當局": "政府",
        "警隊": "警察",
        "警方": "警察",
        "立會": "立法會",
        "冷天氣警告": "寒冷天氣警告",
    }
    def normalize_text_local(text: str) -> str:
        for src, dst in synonym_map.items():
            text = text.replace(src, dst)
        return text
    lowers = [k.lower() for k in keywords]
    for title, body in items_texts:
        text = normalize_text_local(f"{title} {body}").lower()
        for i, kw in enumerate(lowers):
            if kw and kw in text:
                counts[keywords[i]] += 1
    return counts
