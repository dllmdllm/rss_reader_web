
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class Item:
    title: str
    link: str
    pub_dt: datetime | None
    pub_text: str
    source: str
    category: str
    summary: str
    rss_image: str
    extra_images: list[str] = field(default_factory=list)
    image_count: int = 0
    
    # New fields for V2 processing
    content_text: str = ""
    content_html: str = ""
    og_image: str = ""
