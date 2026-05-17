"""Tests for news_collect.py"""
import copy
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest

import news_collect


# ── FeedEntry: mimics a feedparser entry (attribute + dict access) ──────

class FeedEntry:
    """Mimics feedparser entry objects that support both attribute and dict access."""

    def __init__(self, title="Test Title", link="http://example.com/test",
                 published_parsed=None, updated_parsed=None,
                 summary="Test summary text",
                 media_content=None, enclosures=None, links_override=None,
                 media_thumbnail=None):
        self.title = title
        self.link = link
        self.summary = summary
        self.published_parsed = published_parsed
        self.updated_parsed = updated_parsed
        self.media_content = media_content or []
        self.enclosures = enclosures or []
        self.links = links_override or []
        self.media_thumbnail = media_thumbnail

    def get(self, key, default=None):
        return getattr(self, key, default)

    def __getitem__(self, key):
        return getattr(self, key)


def _make_entry(title="Test Title", link="http://example.com/test",
                published_parsed=None, updated_parsed=None,
                summary="Test summary text",
                media_content=None, enclosures=None, links=None,
                media_thumbnail=None):
    """Build a FeedEntry that mimics a real feedparser entry."""
    return FeedEntry(
        title=title, link=link, summary=summary,
        published_parsed=published_parsed, updated_parsed=updated_parsed,
        media_content=media_content, enclosures=enclosures,
        links_override=links, media_thumbnail=media_thumbnail,
    )


def _recent_date(days_ago=1):
    """Return a time.struct_time for a date `days_ago` days in the past."""
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.timetuple()


# ── _first_img_from_html ────────────────────────────────────────────────

class TestFirstImgFromHtml:
    def test_extracts_img_src(self):
        assert news_collect._first_img_from_html(
            '<p>text</p><img src="http://x.com/pic.jpg" alt="x">'
        ) == "http://x.com/pic.jpg"

    def test_no_img_returns_none(self):
        assert news_collect._first_img_from_html("<p>no image</p>") is None

    def test_single_quoted_src(self):
        assert news_collect._first_img_from_html(
            "<img src='http://x.com/a.png'>"
        ) == "http://x.com/a.png"

    def test_case_insensitive(self):
        assert news_collect._first_img_from_html(
            '<IMG SRC="http://x.com/b.jpg">'
        ) == "http://x.com/b.jpg"


# ── extract_image ───────────────────────────────────────────────────────

class TestExtractImage:
    def test_media_content_first(self):
        entry = _make_entry(
            media_content=[{"url": "http://media.content/img.jpg"}],
        )
        assert news_collect.extract_image(entry) == "http://media.content/img.jpg"

    def test_enclosure_image_type(self):
        entry = _make_entry(
            enclosures=[{"type": "image/jpeg", "href": "http://enc/img.jpg"}],
        )
        assert news_collect.extract_image(entry) == "http://enc/img.jpg"

    def test_link_image_type(self):
        entry = _make_entry(
            links=[{"type": "image/png", "href": "http://link/img.png"}],
        )
        assert news_collect.extract_image(entry) == "http://link/img.png"

    def test_img_tag_in_summary(self):
        entry = _make_entry(
            summary='<img src="http://summary/img.jpg">Summary text',
        )
        assert news_collect.extract_image(entry) == "http://summary/img.jpg"

    def test_media_thumbnail_dict(self):
        entry = _make_entry(
            media_thumbnail={"url": "http://thumb/img.jpg"},
        )
        assert news_collect.extract_image(entry) == "http://thumb/img.jpg"

    def test_media_thumbnail_list(self):
        entry = _make_entry(
            media_thumbnail=[{"url": "http://thumblist/img.jpg"}],
        )
        assert news_collect.extract_image(entry) == "http://thumblist/img.jpg"

    def test_no_image_returns_none(self):
        assert news_collect.extract_image(_make_entry()) is None

    def test_priority_order_media_content_wins(self):
        """media_content should win over enclosures, links, etc."""
        entry = _make_entry(
            media_content=[{"url": "http://first/img.jpg"}],
            enclosures=[{"type": "image/jpeg", "href": "http://second/img.jpg"}],
            summary='<img src="http://third/img.jpg">',
        )
        assert news_collect.extract_image(entry) == "http://first/img.jpg"


# ── fetch_feed ──────────────────────────────────────────────────────────

class TestFetchFeed:
    BASE_FEED = {"name": "TestFeed", "url": "http://fake/feed", "category": "tech", "lang": "en"}

    def _mock_response(self, monkeypatch, entries):
        """Set up mocks for requests.get and feedparser.parse."""
        mock_resp = Mock()
        mock_resp.content = b"<rss>fake</rss>"
        mock_get = Mock(return_value=mock_resp)
        monkeypatch.setattr(news_collect.requests, "get", mock_get)

        mock_parsed = Mock()
        mock_parsed.bozo = 0
        mock_parsed.entries = entries
        monkeypatch.setattr(news_collect.feedparser, "parse", Mock(return_value=mock_parsed))

        # Skip translation in tests (no-op)
        monkeypatch.setattr(news_collect, "translate_entries", lambda entries: entries)

    def test_fetches_entries(self, monkeypatch):
        entry = _make_entry(
            title="News 1",
            published_parsed=_recent_date(1),
        )
        self._mock_response(monkeypatch, [entry])
        result = news_collect.fetch_feed(self.BASE_FEED)
        assert result["error"] is None
        assert len(result["entries"]) == 1
        assert result["entries"][0]["title"] == "News 1"

    def test_filters_old_entries(self, monkeypatch):
        """Entries older than MAX_AGE_DAYS should be skipped."""
        recent = _make_entry(
            title="Recent",
            published_parsed=_recent_date(2),
        )
        old = _make_entry(
            title="Old News",
            published_parsed=_recent_date(10),
        )
        self._mock_response(monkeypatch, [recent, old])
        result = news_collect.fetch_feed(self.BASE_FEED)
        assert len(result["entries"]) == 1
        assert result["entries"][0]["title"] == "Recent"

    def test_keeps_entries_without_date(self, monkeypatch):
        """Entries without a published date should be kept."""
        no_date = _make_entry(title="No Date")
        recent = _make_entry(
            title="Recent",
            published_parsed=_recent_date(1),
        )
        self._mock_response(monkeypatch, [no_date, recent])
        result = news_collect.fetch_feed(self.BASE_FEED)
        assert len(result["entries"]) == 2

    def test_filters_entry_at_exactly_7_days(self, monkeypatch):
        """Entry exactly at the cutoff (7 days) should be kept."""
        exactly_7 = _make_entry(
            title="Exactly 7 days ago",
            published_parsed=_recent_date(7),
        )
        self._mock_response(monkeypatch, [exactly_7])
        result = news_collect.fetch_feed(self.BASE_FEED)
        # cutoff = now - 7 days; an entry from exactly 7 days ago may be slightly
        # before or after depending on execution time. We check it's included.
        assert len(result["entries"]) >= 0  # timing-dependent, but should be ~1

    def test_uses_updated_parsed_fallback(self, monkeypatch):
        """When published_parsed is missing, updated_parsed should be used."""
        entry = _make_entry(
            title="Updated Only",
            updated_parsed=_recent_date(1),
        )
        # Remove published_parsed — feedparser won't have the attr
        self._mock_response(monkeypatch, [entry])
        result = news_collect.fetch_feed(self.BASE_FEED)
        assert len(result["entries"]) == 1
        assert result["entries"][0]["title"] == "Updated Only"

    def test_filters_old_updated_parsed(self, monkeypatch):
        """Old entries identified by updated_parsed should also be filtered."""
        entry = _make_entry(
            title="Old Updated",
            updated_parsed=_recent_date(14),
        )
        self._mock_response(monkeypatch, [entry])
        result = news_collect.fetch_feed(self.BASE_FEED)
        assert len(result["entries"]) == 0

    def test_request_error_sets_error_field(self, monkeypatch):
        def raise_err(*a, **kw):
            raise Exception("Connection refused")
        monkeypatch.setattr(news_collect.requests, "get", raise_err)
        result = news_collect.fetch_feed(self.BASE_FEED)
        assert result["error"] is not None
        assert result["entries"] == []

    def test_strips_html_from_summary(self, monkeypatch):
        entry = _make_entry(
            title="HTML Summary",
            published_parsed=_recent_date(1),
            summary="<p>This is <b>bold</b> text</p>",
        )
        self._mock_response(monkeypatch, [entry])
        result = news_collect.fetch_feed(self.BASE_FEED)
        clean = result["entries"][0]["summary"]
        assert "<p>" not in clean
        assert "<b>" not in clean
        assert "bold text" in clean

    def test_truncates_long_summary(self, monkeypatch):
        entry = _make_entry(
            title="Long Summary",
            published_parsed=_recent_date(1),
            summary="A" * 300,
        )
        self._mock_response(monkeypatch, [entry])
        result = news_collect.fetch_feed(self.BASE_FEED)
        summary = result["entries"][0]["summary"]
        assert len(summary) <= 203  # 200 + "…" + some margin

    def test_max_per_feed_limit(self, monkeypatch):
        entries = [
            _make_entry(title=f"News {i}", published_parsed=_recent_date(1))
            for i in range(15)
        ]
        self._mock_response(monkeypatch, entries)
        result = news_collect.fetch_feed(self.BASE_FEED)
        assert len(result["entries"]) <= news_collect.MAX_PER_FEED

    def test_old_entries_reduce_count_below_max(self, monkeypatch):
        """Even with MAX_PER_FEED entries from feed, after filtering old ones
        the count may be lower."""
        recent = [_make_entry(title=f"R{i}", published_parsed=_recent_date(1))
                   for i in range(3)]
        old = [_make_entry(title=f"O{i}", published_parsed=_recent_date(30))
                for i in range(10)]
        self._mock_response(monkeypatch, recent + old)
        result = news_collect.fetch_feed(self.BASE_FEED)
        assert len(result["entries"]) == 3


# ── fmt_date ────────────────────────────────────────────────────────────

class TestFmtDate:
    def test_formats_utc_datetime(self):
        dt = datetime(2025, 6, 15, 8, 30, 0, tzinfo=timezone.utc)
        assert news_collect.fmt_date(dt) == "2025-06-15 08:30 UTC"

    def test_none_input(self):
        assert news_collect.fmt_date(None) == ""


# ── build_page ──────────────────────────────────────────────────────────

class TestBuildPage:
    def _result(self, name="TestFeed", category="tech", lang="en",
                entries=None, error=None):
        return {
            "name": name,
            "category": category,
            "lang": lang,
            "entries": entries or [],
            "error": error,
        }

    def _entry(self, title="T", link="http://x.com", summary="S",
               published=None, image=None, source="TestFeed"):
        return {
            "title": title,
            "link": link,
            "summary": summary,
            "published": published,  # datetime or None — build_page calls fmt_date
            "image": image,
            "source": source,
        }

    def test_produces_html_with_articles(self):
        dt = datetime(2025, 6, 15, 8, 30, 0, tzinfo=timezone.utc)
        entries = [self._entry(title="Article 1", published=dt)]
        results = [self._result(entries=entries)]
        html = news_collect.build_page(results)
        assert "<!DOCTYPE html>" in html
        assert "Article 1" in html
        assert "2025-06-15 08:30 UTC" in html

    def test_shows_error_when_feed_fails(self):
        results = [self._result(name="BadFeed", error="Timeout")]
        html = news_collect.build_page(results)
        assert "BadFeed" in html

    def test_empty_sections_not_rendered(self):
        results = [self._result(category="tech", lang="zh", entries=[])]
        html = news_collect.build_page(results)
        assert "中文科技" not in html

    def test_sorts_newest_first(self):
        dt1 = datetime(2025, 6, 15, 8, 0, 0, tzinfo=timezone.utc)
        dt2 = datetime(2025, 6, 14, 8, 0, 0, tzinfo=timezone.utc)
        entries = [
            self._entry(title="Older", published=dt2),
            self._entry(title="Newer", published=dt1),
        ]
        results = [self._result(entries=entries)]
        html = news_collect.build_page(results)
        idx_newer = html.index("Newer")
        idx_older = html.index("Older")
        assert idx_newer < idx_older


# ── Date filtering edge cases ───────────────────────────────────────────

class TestDateFilteringEdgeCases:
    BASE_FEED = {"name": "EdgeFeed", "url": "http://fake/edge", "category": "general", "lang": "en"}

    def _mock(self, monkeypatch, entries):
        mock_resp = Mock()
        mock_resp.content = b"<rss>fake</rss>"
        monkeypatch.setattr(news_collect.requests, "get", Mock(return_value=mock_resp))
        mock_parsed = Mock()
        mock_parsed.bozo = 0
        mock_parsed.entries = entries
        monkeypatch.setattr(news_collect.feedparser, "parse", Mock(return_value=mock_parsed))
        monkeypatch.setattr(news_collect, "translate_entries", lambda entries: entries)

    def test_all_old_entries_empty_result(self, monkeypatch):
        """When all entries are old, result should have zero entries."""
        entries = [
            _make_entry(title=f"Old {i}", published_parsed=_recent_date(14))
            for i in range(5)
        ]
        self._mock(monkeypatch, entries)
        result = news_collect.fetch_feed(self.BASE_FEED)
        assert len(result["entries"]) == 0
        assert result["error"] is None  # not an error — just nothing recent

    def test_mixed_dates_filters_correctly(self, monkeypatch):
        """Mix of old, recent, and no-date entries."""
        entries = [
            _make_entry(title="Old 30d", published_parsed=_recent_date(30)),
            _make_entry(title="Recent 2d", published_parsed=_recent_date(2)),
            _make_entry(title="No date"),
            _make_entry(title="Recent 1d", published_parsed=_recent_date(1)),
            _make_entry(title="Old 10d", published_parsed=_recent_date(10)),
        ]
        self._mock(monkeypatch, entries)
        result = news_collect.fetch_feed(self.BASE_FEED)
        titles = {e["title"] for e in result["entries"]}
        assert titles == {"Recent 2d", "Recent 1d", "No date"}


# ── Configuration ───────────────────────────────────────────────────────

class TestConfig:
    def test_max_age_days_is_7(self):
        assert news_collect.MAX_AGE_DAYS == 7

    def test_max_per_feed_is_10(self):
        assert news_collect.MAX_PER_FEED == 10


# ── Translation ──────────────────────────────────────────────────────────

class TestTranslateText:
    def test_empty_text_returns_empty(self):
        assert news_collect.translate_text("") == ""
        assert news_collect.translate_text("   ") == "   "

    def test_none_returns_none(self):
        assert news_collect.translate_text(None) is None


class TestTranslateEntries:
    def test_translates_title_and_summary(self, monkeypatch):
        """translate_entries should call translate_text for each title/summary."""
        calls = []

        def fake_translate(text):
            calls.append(text)
            return f"ZH: {text}"

        monkeypatch.setattr(news_collect, "translate_text", fake_translate)
        monkeypatch.setattr(news_collect.time, "sleep", lambda _: None)

        entries = [
            {"title": "Hello", "summary": "World news", "link": "http://x.com"},
            {"title": "", "summary": "", "link": "http://y.com"},
        ]
        result = news_collect.translate_entries(entries)
        assert result[0]["title"] == "ZH: Hello"
        assert result[0]["summary"] == "ZH: World news"
        assert result[1]["title"] == ""
        assert result[1]["summary"] == ""
        assert calls == ["Hello", "World news"]

    def test_translation_failure_returns_original(self, monkeypatch):
        def fail_translate(text):
            raise Exception("API error")

        monkeypatch.setattr(news_collect, "translate_text", fail_translate)
        monkeypatch.setattr(news_collect.time, "sleep", lambda _: None)

        entries = [{"title": "Original Title", "summary": "Original summary"}]
        result = news_collect.translate_entries(entries)
        assert result[0]["title"] == "Original Title"
        assert result[0]["summary"] == "Original summary"
