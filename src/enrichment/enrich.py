"""Background enrichment worker for articles.

This worker fetches unenriched articles, generates short summaries,
extracts tags, and captures a primary image for downstream display.
"""
from __future__ import annotations

import argparse
import html
import logging
import os
import re
import sqlite3
import textwrap
import time
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import List, Optional, Sequence
from urllib.parse import urlparse, urlunparse
from urllib.request import urlopen


LOGGER = logging.getLogger(__name__)


@dataclass
class Article:
    id: int
    title: str
    body: str
    summary: Optional[str]
    source_url: Optional[str] = None
    enclosure_url: Optional[str] = None
    media_url: Optional[str] = None
    image_url: Optional[str] = None


class DatabaseClient:
    """Very small SQLite helper dedicated to enrichment tasks."""

    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row

    def fetch_unenriched_articles(self, limit: int = 20) -> List[Article]:
        cursor = self.conn.execute(
            textwrap.dedent(
                """
                SELECT id, title, body, summary, source_url, enclosure_url, media_url, image_url
                FROM articles
                WHERE summary IS NULL OR TRIM(summary) = ''
                ORDER BY id DESC
                LIMIT ?
                """
            ),
            (limit,),
        )
        rows = cursor.fetchall()
        articles = [Article(**dict(row)) for row in rows]
        LOGGER.debug("Fetched %s unenriched articles", len(articles))
        return articles

    def update_article_enrichment(self, article_id: int, summary: str, image_url: Optional[str]) -> None:
        LOGGER.info("Persisting summary and image for article %s", article_id)
        self.conn.execute(
            "UPDATE articles SET summary = ?, image_url = COALESCE(?, image_url) WHERE id = ?",
            (summary, image_url, article_id),
        )
        self.conn.commit()

    def ensure_tag(self, name: str) -> int:
        cursor = self.conn.execute("SELECT id FROM tags WHERE name = ?", (name,))
        row = cursor.fetchone()
        if row:
            return row[0]

        slug = slugify(name)
        LOGGER.debug("Creating tag '%s'", name)
        cursor = self.conn.execute(
            "INSERT INTO tags (name, slug) VALUES (?, ?)",
            (name, slug),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def attach_tags(self, article_id: int, tags: Sequence[str]) -> None:
        LOGGER.info("Attaching %s tags to article %s", len(tags), article_id)
        for tag in tags:
            tag_id = self.ensure_tag(tag)
            self.conn.execute(
                "INSERT OR IGNORE INTO article_tags (article_id, tag_id) VALUES (?, ?)",
                (article_id, tag_id),
            )
        self.conn.commit()


class Summarizer:
    """Simple summarizer facade that can talk to an API or fall back locally."""

    def __init__(self, api_url: Optional[str] = None, api_key: Optional[str] = None, target_words: int = 65):
        self.api_url = api_url
        self.api_key = api_key
        self.target_words = target_words

    def summarize(self, title: str, body: str) -> str:
        text = f"{title}. {body}" if title not in body else body
        if self.api_url and self.api_key:
            LOGGER.debug("Using remote summarization API at %s", self.api_url)
            return self._call_api(text)
        return self._local_summarize(text)

    def _call_api(self, text: str) -> str:
        # Placeholder for real API call. Implementors can swap this with requests.
        LOGGER.warning("Remote summarization API is not implemented; falling back to local summarizer")
        return self._local_summarize(text)

    def _local_summarize(self, text: str) -> str:
        words = re.findall(r"\w+[^\s]*", text)
        target = max(min(self.target_words, 80), 50)
        snippet = words[:target]
        snippet_text = " ".join(snippet)
        return textwrap.shorten(snippet_text, width=800, placeholder="…")


class KeywordExtractor:
    """Extract lightweight keywords for tagging."""

    STOPWORDS = {
        "the",
        "and",
        "a",
        "an",
        "to",
        "of",
        "in",
        "for",
        "on",
        "at",
        "by",
        "with",
        "from",
        "that",
        "this",
        "it",
        "as",
        "is",
        "are",
    }

    def extract(self, text: str, limit: int = 8) -> List[str]:
        tokens = [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", text)]
        filtered = [t for t in tokens if t not in self.STOPWORDS]
        counts = Counter(filtered)
        keywords = [word for word, _ in counts.most_common(limit)]
        LOGGER.debug("Extracted keywords: %s", keywords)
        return keywords


class OgImageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.og_image: Optional[str] = None

    def handle_starttag(self, tag: str, attrs):
        if tag != "meta":
            return
        attrs_dict = {k.lower(): v for k, v in attrs}
        property_attr = attrs_dict.get("property") or attrs_dict.get("name")
        if property_attr and property_attr.lower() == "og:image":
            self.og_image = attrs_dict.get("content")


class ImageResolver:
    def __init__(self, cdn_prefix: Optional[str] = None):
        self.cdn_prefix = cdn_prefix.rstrip("/") if cdn_prefix else None

    def resolve(self, article: Article) -> Optional[str]:
        for candidate in (article.image_url, article.enclosure_url, article.media_url):
            if candidate:
                return self._sanitize(candidate)

        if article.source_url:
            og_image = self._fetch_og_image(article.source_url)
            if og_image:
                return self._sanitize(og_image)
        return None

    def _fetch_og_image(self, url: str) -> Optional[str]:
        try:
            with urlopen(url, timeout=10) as resp:
                content_type = resp.headers.get("content-type", "")
                if "text/html" not in content_type:
                    return None
                html_content = resp.read().decode("utf-8", errors="ignore")
        except Exception as exc:  # pragma: no cover - best-effort fetch
            LOGGER.debug("Failed to fetch OG image from %s: %s", url, exc)
            return None

        parser = OgImageParser()
        parser.feed(html_content)
        return parser.og_image

    def _sanitize(self, url: str) -> str:
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"http", "https"}:
            parsed = parsed._replace(scheme="https")
        cleaned = parsed._replace(query="", fragment="")
        sanitized = urlunparse(cleaned)
        if self.cdn_prefix:
            path = sanitized.split("//", 1)[-1]
            sanitized = f"{self.cdn_prefix}/{path}"
        return sanitized


class EnrichmentWorker:
    def __init__(self, db: DatabaseClient, summarizer: Summarizer, extractor: KeywordExtractor, resolver: ImageResolver):
        self.db = db
        self.summarizer = summarizer
        self.extractor = extractor
        self.resolver = resolver

    def run_once(self, limit: int = 20) -> int:
        articles = self.db.fetch_unenriched_articles(limit=limit)
        for article in articles:
            summary = self.summarizer.summarize(article.title, article.body)
            tags = self.extractor.extract(f"{article.title} {article.body}")
            image_url = self.resolver.resolve(article)

            self.db.update_article_enrichment(article.id, summary, image_url)
            self.db.attach_tags(article.id, tags)
        return len(articles)

    def run_forever(self, interval_seconds: int = 300, limit: int = 20) -> None:
        LOGGER.info("Starting enrichment worker loop (interval=%ss)", interval_seconds)
        while True:
            processed = self.run_once(limit=limit)
            if processed:
                LOGGER.info("Enriched %s articles", processed)
            else:
                LOGGER.debug("No unenriched articles found")
            time.sleep(interval_seconds)


def slugify(value: str) -> str:
    value = html.unescape(value).lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def build_worker_from_env() -> EnrichmentWorker:
    db_path = os.getenv("ENRICH_DB_PATH", "articles.db")
    db = DatabaseClient(db_path)
    summarizer = Summarizer(
        api_url=os.getenv("SUMMARY_API_URL"),
        api_key=os.getenv("SUMMARY_API_KEY"),
        target_words=int(os.getenv("SUMMARY_TARGET_WORDS", "65")),
    )
    extractor = KeywordExtractor()
    resolver = ImageResolver(cdn_prefix=os.getenv("CDN_PREFIX"))
    return EnrichmentWorker(db, summarizer, extractor, resolver)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the article enrichment worker")
    parser.add_argument("--once", action="store_true", help="Run a single enrichment pass and exit")
    parser.add_argument("--limit", type=int, default=20, help="Max unenriched articles to process per pass")
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.getenv("ENRICH_POLL_INTERVAL", "300")),
        help="Seconds between polling for new articles",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    args = parse_args(argv)
    worker = build_worker_from_env()

    if args.once:
        worker.run_once(limit=args.limit)
    else:
        worker.run_forever(interval_seconds=args.interval, limit=args.limit)


if __name__ == "__main__":
    main()
