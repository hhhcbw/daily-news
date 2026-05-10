"""Daily news collector — fetches RSS feeds and generates a static HTML page."""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import feedparser
import requests
from jinja2 import Template

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── RSS Source definitions ──────────────────────────────────────────────

FEEDS = [
    # English tech
    {"name": "Hacker News", "url": "https://hnrss.org/frontpage?count=12",
     "category": "tech", "lang": "en"},
    {"name": "TechCrunch", "url": "https://techcrunch.com/feed/",
     "category": "tech", "lang": "en"},
    # Chinese tech
    {"name": "36氪", "url": "https://36kr.com/feed",
     "category": "tech", "lang": "zh"},
    {"name": "少数派", "url": "https://sspai.com/feed",
     "category": "tech", "lang": "zh"},
    # English general
    {"name": "BBC News", "url": "https://feeds.bbci.co.uk/news/rss.xml",
     "category": "general", "lang": "en"},
    {"name": "The Guardian", "url": "https://www.theguardian.com/world/rss",
     "category": "general", "lang": "en"},
    # Chinese general (English-language outlet covering China & world)
    {"name": "China Daily", "url": "https://www.chinadaily.com.cn/rss/world_rss.xml",
     "category": "general", "lang": "en"},
]

REQUEST_TIMEOUT = 20
MAX_PER_FEED = 10
OUTPUT_DIR = Path(__file__).resolve().parent / "docs"
OUTPUT_FILE = OUTPUT_DIR / "index.html"

# ── Image extraction helpers ────────────────────────────────────────────

def _first_img_from_html(html: str) -> Optional[str]:
    """Extract the first <img src> from an HTML string."""
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
    return m.group(1) if m else None

def extract_image(entry: dict) -> Optional[str]:
    """Try to extract a lead image from a feedparser entry."""
    # 1) media_content
    for media in entry.get("media_content", []) or []:
        url = media.get("url")
        if url:
            return url

    # 2) enclosures with image type
    for enc in entry.get("enclosures", []) or []:
        if "image" in (enc.get("type") or ""):
            return enc.get("href")

    # 3) links with image type
    for link in entry.get("links", []) or []:
        if "image" in (link.get("type") or ""):
            return link.get("href")

    # 4) <img> in summary / content / description
    for field in ("summary", "content", "description"):
        val = entry.get(field)
        if val:
            html = val[0].get("value", "") if isinstance(val, list) else str(val)
            img = _first_img_from_html(html)
            if img:
                return img

    # 5) media_thumbnail
    thumb = entry.get("media_thumbnail")
    if thumb:
        if isinstance(thumb, list) and thumb:
            return thumb[0].get("url")
        if isinstance(thumb, dict):
            return thumb.get("url")

    return None

# ── Fetch & parse ───────────────────────────────────────────────────────

def fetch_feed(feed_def: dict) -> dict:
    """Fetch one RSS feed and return parsed entries."""
    result = {
        "name": feed_def["name"],
        "category": feed_def["category"],
        "lang": feed_def["lang"],
        "entries": [],
        "error": None,
    }
    try:
        resp = requests.get(feed_def["url"], timeout=REQUEST_TIMEOUT,
                           headers={"User-Agent": "DailyNewsCollector/1.0"})
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        if parsed.bozo and not parsed.entries:
            log.warning("Feed %s parse warning (no entries): %s", feed_def["name"], parsed.bozo_exception)
            result["error"] = "Parse error"
            return result

        for entry in parsed.entries[:MAX_PER_FEED]:
            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                published = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)

            summary = entry.get("summary") or entry.get("description") or ""
            if isinstance(summary, str):
                clean = re.sub(r"<[^>]+>", "", summary).strip()
            else:
                clean = str(summary).strip()
            # Strip Hacker News metadata lines
            clean = re.sub(r"(?:Article|Comments)\s+URL:\s*\S+\s*", "", clean)
            clean = re.sub(r"Points:\s*\d+\s*", "", clean)
            clean = re.sub(r"#\s*Comments:\s*\d+\s*", "", clean)
            clean = re.sub(r"\n{2,}", "\n", clean).strip()
            # Truncate
            if len(clean) > 200:
                clean = clean[:200] + "…"

            result["entries"].append({
                "title": entry.get("title", "Untitled").strip(),
                "link": entry.get("link", "#"),
                "summary": clean,
                "published": published,
                "image": extract_image(entry),
            })

        log.info("Fetched %s: %d entries", feed_def["name"], len(result["entries"]))
    except Exception as e:
        log.error("Failed to fetch %s: %s", feed_def["name"], e)
        result["error"] = str(e)

    return result

# ── HTML generation ─────────────────────────────────────────────────────

HTML_TEMPLATE = Template(r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>每日新闻汇总 — {{ date_str }}</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans SC", sans-serif; background: #f5f5f5; color: #222; line-height: 1.6; }
  header { background: linear-gradient(135deg, #1a1a2e, #16213e); color: #fff; padding: 32px 16px; text-align: center; }
  header h1 { font-size: 1.8rem; margin-bottom: 4px; }
  header p { opacity: .75; font-size: .9rem; }
  .container { max-width: 1200px; margin: 0 auto; padding: 24px 16px; }
  .section { margin-bottom: 40px; }
  .section-title { font-size: 1.4rem; margin-bottom: 16px; padding-bottom: 8px; border-bottom: 3px solid #1a1a2e; display: flex; align-items: center; gap: 8px; }
  .section-title .badge { font-size: .75rem; background: #1a1a2e; color: #fff; padding: 2px 10px; border-radius: 12px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 16px; }
  .card { background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,.08); transition: box-shadow .2s; display: flex; flex-direction: column; }
  .card:hover { box-shadow: 0 4px 20px rgba(0,0,0,.14); }
  .card-img { width: 100%; height: 180px; object-fit: cover; background: #e0e0e0; display: block; }
  .card-img-placeholder { width: 100%; height: 180px; background: linear-gradient(135deg, #e0e0e0, #ccc); display: flex; align-items: center; justify-content: center; color: #999; font-size: 2.5rem; }
  .card-body { padding: 16px; flex: 1; display: flex; flex-direction: column; }
  .card-body h3 { font-size: 1.05rem; margin-bottom: 8px; line-height: 1.4; }
  .card-body h3 a { color: #1a1a2e; text-decoration: none; }
  .card-body h3 a:hover { text-decoration: underline; color: #1a56db; }
  .card-meta { font-size: .8rem; color: #888; margin-bottom: 8px; }
  .card-summary { font-size: .88rem; color: #444; flex: 1; }
  .source-tag { display: inline-block; font-size: .72rem; background: #e8e8e8; color: #555; padding: 1px 8px; border-radius: 10px; margin-right: 6px; }
  .error-note { background: #fff3cd; padding: 12px 16px; border-radius: 8px; color: #856404; font-size: .85rem; margin-bottom: 16px; }
  footer { text-align: center; padding: 24px; color: #aaa; font-size: .8rem; }
  @media (max-width: 400px) { .grid { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<header>
  <h1>每日新闻汇总</h1>
  <p>更新于 {{ date_str }}</p>
</header>
<div class="container">
{% if errors %}
<div class="error-note">⚠ 部分新闻源暂时无法访问：{{ errors | join(", ") }}</div>
{% endif %}

{% for section in sections %}
<div class="section">
  <h2 class="section-title">{{ section.label }} <span class="badge">{{ section.entries | length }} 条</span></h2>
  <div class="grid">
  {% for item in section.entries %}
    <div class="card">
      {% if item.image %}
      <img class="card-img" src="{{ item.image }}" alt="" loading="lazy" onerror="this.style.display='none'">
      {% else %}
      <div class="card-img-placeholder">📰</div>
      {% endif %}
      <div class="card-body">
        <h3><a href="{{ item.link }}" target="_blank" rel="noopener">{{ item.title }}</a></h3>
        <div class="card-meta">
          <span class="source-tag">{{ item.source }}</span>
          {% if item.published %}{{ item.published }}{% endif %}
        </div>
        {% if item.summary %}<p class="card-summary">{{ item.summary }}</p>{% endif %}
      </div>
    </div>
  {% endfor %}
  </div>
</div>
{% endfor %}
</div>
<footer>Generated by Daily News Collector &middot; news via RSS feeds</footer>
</body>
</html>""")

def fmt_date(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M UTC") if d else ""

def build_page(results: list[dict]) -> str:
    """Build the full HTML page from feed results."""
    # Group entries by section
    sections_map = {
        "tech_en": {"label": "💻 科技新闻 · English Tech", "entries": []},
        "tech_zh": {"label": "💻 科技新闻 · 中文科技", "entries": []},
        "general_en": {"label": "🌍 时事新闻 · World News", "entries": []},
        "general_zh": {"label": "🌍 时事新闻 · 中文时事", "entries": []},
    }

    for feed in results:
        key = f"{feed['category']}_{feed['lang']}"
        section = sections_map.get(key)
        if section is None:
            continue
        for e in feed["entries"]:
            section["entries"].append({
                **e,
                "source": feed["name"],
                "published": fmt_date(e["published"]) if e["published"] else "",
            })

    # Sort each section by time (newest first), entries without date go last
    for sec in sections_map.values():
        sec["entries"].sort(key=lambda x: x.get("published") or "", reverse=True)

    errors = [r["name"] for r in results if r["error"]]
    sections = [s for s in sections_map.values() if s["entries"]]

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return HTML_TEMPLATE.render(sections=sections, errors=errors, date_str=now_str)

# ── Main ─────────────────────────────────────────────────────────────────

def main():
    log.info("Starting daily news collection…")
    results = []
    for feed_def in FEEDS:
        results.append(fetch_feed(feed_def))

    total_entries = sum(len(r["entries"]) for r in results)
    log.info("Total entries collected: %d", total_entries)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    html = build_page(results)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    log.info("Page written to %s (%d bytes)", OUTPUT_FILE, len(html))

    errors = [r for r in results if r["error"]]
    if errors:
        log.warning("%d feed(s) had errors, page still generated", len(errors))

if __name__ == "__main__":
    main()
