# HK RSS Reader Web

A modern, mobile-first RSS news aggregator designed with a premium "Glassmorphism" aesthetic. Built with Python for the backend and pure Vanilla JS/CSS for the frontend, it generates a static single-page application (SPA) that is fast, responsive, and beautiful.

## ✨ Features

### 🎨 UI/UX
- **Glassmorphism Design**: Frosted glass effects, vibrant gradients, and smooth transitions.
- **Siri Flow Cards**: Expanded news cards feature a "Siri-like" breathing gradient border and glow effect for a premium reading experience.
- **Mobile-First**: Optimised touch targets, layouts, and typography for mobile devices (iPhone/Android).
- **Dark Mode Friendly**: Deep blue/black themes that look great on OLED screens.

### 📰 Content Engine
- **Full-Text Extraction**: Automatically scrapes and parses full article content from supported sources (HK01, MingPao, Unwire, etc.), bypassing "Read More" buttons.
- **Smart Parsing**: 
    - **HK01**: Advanced JSON parsing to extract high-res images, galleries, and accurate timestamps.
    - **CNBeta**: Auto-converts Simplified Chinese to Traditional Chinese.
- **Image Caching**: Downloads and optimizes article images locally to prevent hotlinking issues and ensure fast loading.

### ⚙️ Functionality
- **Categorization**: Auto-sorts news into **News (新聞)**, **Intl (國際)**, **Ent (娛樂)**, **Tech (科技)**, etc.
- **Source Filtering**: Filter specifically by source (e.g., show only existing "HK01" or "MingPao" news).
- **Search & Highlighting**: Real-time search with keyword highlighting in titles and snippets.
- **Auto-Update**: Configured with GitHub Actions to run every 5 minutes, ensuring the latest news is always live.

---

## 🚀 Getting Started

### Prerequisites
- Python 3.10+
- `pip`

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/dllmdllm/rss_reader_web.git
   cd rss_reader_web
   ```

2. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```
   *Key dependencies: `httpx`, `jinja2`, `lxml`, `opencc`, `pillow`.*

3. **Configure Feeds**
   Edit `feeds.json` to add or remove RSS sources:
   ```json
   [
       "https://www.hk01.com/rss",
       "https://news.mingpao.com/rss/pns/s00001.xml",
       ...
   ]
   ```

### Running Locally

To fetch news, parse content, and generate the static site:

```bash
python build.py
```

This will:
1. Fetch RSS feeds concurrently.
2. Scrape full content for new items.
3. Download images to `images/`.
4. Generate `index.html` in the root directory.

Open `index.html` in your browser to view the site.

---

## 🛠 Project Structure

- **`build.py`**: Main entry point. Orchestrates fetching, parsing, and rendering.
- **`rss_core/`**: Core logic modules.
    - `fetcher.py`: Async HTTP client with caching.
    - `feed_parser.py`: Parses RSS XML and handles site-specific extracting (HK01 JSON, generic HTML).
    - `parser.py`: Text cleaning and image extraction logic.
- **`templates/`**: Jinja2 templates.
    - `index_template.html`: The monolithic template containing HTML, CSS, and JS.
- **`data/`**: JSON caches (`feed_cache.json`, `fulltext_cache.json`) to speed up subsequent builds.
- **`.github/workflows/`**: CI/CD configuration for automatic updates.

---

## 🤖 Automation

The project includes a GitHub Actions workflow (`.github/workflows/update.yml`) that:
1. Runs every **5 minutes**.
2. Executes `build.py`.
3. Commits and pushes the updated `index.html` and cache files back to the repository.
4. Deploys correctly to GitHub Pages.

---

## 📝 License
Proprietary / Personal Use.
