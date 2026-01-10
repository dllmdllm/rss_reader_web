# RSS Reader Web — Context Summary (2026-01-09 HKT)

## Project
- Path: `/mnt/c/Users/Nary/PY_Project/rss_reader_web`
- Main generator: `generate_site.py`
- Image compression: `compress_images.py`
- Auto update: `update_site.sh`
- Output: `index.html`

## Sources & Tags
- **News**: RTHK (local), Mingpao (multiple RSS), on.cc (news), singtao (news), hk01 (news)
- **International**: RTHK international + greater china RSS, mingpao s00004/s00005, on.cc intnews, singtao realtime china/world, hk01 channel/19 + zone/5
- **Entertainment**: mingpao s00007, on.cc entertainment, singtao entertainment, hk01 zone/2
- **Tech**: cnbeta

### RTHK RSS (corrected)
- Local: `https://rthk9.rthk.hk/rthk/news/rss/c_expressnews_clocal.xml`
- International: `https://rthk9.rthk.hk/rthk/news/rss/c_expressnews_cinternational.xml`
- Greater China: `https://rthk9.rthk.hk/rthk/news/rss/c_expressnews_greaterchina.xml`

## Key Behaviors
- **Default lookback**: 6 hours
- **Refresh**: 10 minutes
- **Mixed mode**: per-source caps (cnbeta 50; singtao news 2 hours; others follow lookback)
- **Dedup**: title similarity
- **Focus**: click a card → expand + focus; scroll does not change focus
- **Scroll alignment**: marquee click & collapse buttons align to card top
- **Smooth scroll**: custom easing for scroll-to-top/scroll-to-card (prefers-reduced-motion respected)

## UI / UX
- Title marquee + keyword marquee
- Tags as `<button>` (fix iOS click issue)
- Title font == content font (uses `--content-font`), A-/A+ controls affect both
- Seen: removes “已讀” label; seen cards have lighter weight; marquee seen titles also lighter
- Collapse buttons: both left & right “▴” on each card
- Update marker inserted based on last refresh (`localStorage`): “～～～ 更新分隔 ～～～”

## Image Handling
- Download image with source prefixes (rthk_, mingpao_, hk01_, oncc_, singtao_, cnbeta_)
- RTHK: if RSS has no image, try `og:image` from fulltext
- Singtao: remove first image + dedupe + cap 20 images; remove ads/related blocks
- HK01: parse `__NEXT_DATA__`, extract text + extra images
- Images are compressed post-generate (`compress_images.py`):
  - Max width: 720
  - Target size: 200KB
  - Converts PNG to WEBP
  - Marks `_OK` in filename
- Image cache stored in `data/image_cache.json`

## Notable Fixes
- Fixed malformed RSS links (`normalize_link` strips `&quot; target=...`)
- RTHK og:image fallback
- iOS tag clicks fixed (span → button)
- Collapse button outside content to avoid HTML injection issues

## Known Constraints / Notes
- WSL DNS sometimes fails; Windows curl works
- Large image churn on each regenerate is expected (content changes)

## Commands
- Generate:
  ```bash
  python3 /mnt/c/Users/Nary/PY_Project/rss_reader_web/generate_site.py --lookback-hours 6 --refresh-seconds 600
  ```
- Compress:
  ```bash
  /mnt/c/Users/Nary/.venv/rss_reader_web/bin/python /mnt/c/Users/Nary/PY_Project/rss_reader_web/compress_images.py
  ```
- Push:
  ```bash
  git -C /mnt/c/Users/Nary/PY_Project/rss_reader_web add generate_site.py index.html images && \
  git -C /mnt/c/Users/Nary/PY_Project/rss_reader_web add -f data/image_cache.json && \
  git -C /mnt/c/Users/Nary/PY_Project/rss_reader_web commit -m "Update" && \
  git -C /mnt/c/Users/Nary/PY_Project/rss_reader_web push
  ```

## User Preferences
- Cantonese responses, no AI tone, emojis allowed
- Hong Kong timezone (HKT)
- Do not show “已讀” text label
- Title + content font sizes always synced
- Smooth scroll (no sudden jumps)
- Tags must be tappable on iOS
- RTHK images must show
