"""RSS ingestion job for pulling articles into SQLite tables.

Fetches a configured RSS feed on an interval, parses entries with feedparser,
normalizes timestamps to UTC, and upserts both raw and normalized article
records. Lightweight logging is included to track fetch results and failures.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from typing import Iterable, List, Mapping, Optional

import feedparser
import schedule
from dateutil import parser as date_parser

DEFAULT_FEED_URL = "https://rss.feedspot.com/folder/5hvIsGIZ5g==/rss/rsscombiner"
DEFAULT_INTERVAL_SECONDS = 60 * 60  # hourly by default
DEFAULT_SOURCE = "feedspot-folder"
DEFAULT_DB_PATH = os.environ.get("INGESTION_DB_PATH", "ingestion.db")


logger = logging.getLogger("rss_ingest")


def ensure_tables(conn: sqlite3.Connection) -> None:
    """Create articles_raw and articles tables if they do not already exist."""

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS articles_raw (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guid TEXT NOT NULL,
            link TEXT NOT NULL,
            title TEXT,
            description TEXT,
            published_at TEXT,
            source TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            media_urls TEXT,
            raw_entry TEXT,
            UNIQUE(guid, link)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guid TEXT NOT NULL,
            link TEXT NOT NULL,
            title TEXT,
            summary TEXT,
            published_at TEXT,
            source TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            media_urls TEXT,
            UNIQUE(guid, link)
        )
        """
    )
    conn.commit()


def parse_datetime(entry: Mapping[str, object]) -> Optional[str]:
    """Convert date fields from a feed entry to an ISO UTC string."""

    time_struct = entry.get("published_parsed") or entry.get("updated_parsed")
    dt: Optional[datetime] = None

    if time_struct:
        dt = datetime.fromtimestamp(time.mktime(time_struct), tz=timezone.utc)
    else:
        date_str = entry.get("published") or entry.get("updated")
        if date_str:
            try:
                parsed = date_parser.parse(str(date_str))
                if not parsed.tzinfo:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                dt = parsed.astimezone(timezone.utc)
            except (ValueError, TypeError) as exc:  # best-effort parse
                logger.warning("Failed to parse date '%s': %s", date_str, exc)

    return dt.isoformat() if dt else None


def extract_media_urls(entry: Mapping[str, object]) -> List[str]:
    """Collect enclosure/media URLs from an entry."""

    urls: List[str] = []

    # Enclosures
    for link_obj in entry.get("links", []) or []:
        href = link_obj.get("href") if isinstance(link_obj, Mapping) else None
        if href and link_obj.get("rel") == "enclosure":
            urls.append(str(href))

    # media:content entries
    for media in entry.get("media_content", []) or []:
        url = media.get("url") if isinstance(media, Mapping) else None
        if url:
            urls.append(str(url))

    # media:thumbnail entries
    for media in entry.get("media_thumbnail", []) or []:
        url = media.get("url") if isinstance(media, Mapping) else None
        if url:
            urls.append(str(url))

    # Deduplicate while preserving order
    seen = set()
    deduped: List[str] = []
    for url in urls:
        if url not in seen:
            deduped.append(url)
            seen.add(url)

    return deduped


def upsert_article(conn: sqlite3.Connection, entry: Mapping[str, object], source: str) -> None:
    """Upsert raw and normalized records for a single feed entry."""

    guid_value = entry.get("id") or entry.get("guid") or entry.get("link")
    link_value = entry.get("link") or guid_value

    if not guid_value and not link_value:
        logger.warning("Skipping entry with neither guid nor link: %s", entry)
        return

    guid = str(guid_value or link_value)
    link = str(link_value or guid_value)
    fetched_at = datetime.now(timezone.utc).isoformat()
    published_at = parse_datetime(entry)
    media_urls = extract_media_urls(entry)

    raw_entry = json.dumps(entry, default=str)
    description = entry.get("description") or entry.get("summary")

    conn.execute(
        """
        INSERT INTO articles_raw (guid, link, title, description, published_at, source, fetched_at, media_urls, raw_entry)
        VALUES (:guid, :link, :title, :description, :published_at, :source, :fetched_at, :media_urls, :raw_entry)
        ON CONFLICT(guid, link) DO UPDATE SET
            title=excluded.title,
            description=excluded.description,
            published_at=excluded.published_at,
            source=excluded.source,
            fetched_at=excluded.fetched_at,
            media_urls=excluded.media_urls,
            raw_entry=excluded.raw_entry
        """,
        {
            "guid": guid,
            "link": link,
            "title": entry.get("title"),
            "description": description,
            "published_at": published_at,
            "source": source,
            "fetched_at": fetched_at,
            "media_urls": json.dumps(media_urls),
            "raw_entry": raw_entry,
        },
    )

    conn.execute(
        """
        INSERT INTO articles (guid, link, title, summary, published_at, source, fetched_at, media_urls)
        VALUES (:guid, :link, :title, :summary, :published_at, :source, :fetched_at, :media_urls)
        ON CONFLICT(guid, link) DO UPDATE SET
            title=excluded.title,
            summary=excluded.summary,
            published_at=excluded.published_at,
            source=excluded.source,
            fetched_at=excluded.fetched_at,
            media_urls=excluded.media_urls
        """,
        {
            "guid": guid,
            "link": link,
            "title": entry.get("title"),
            "summary": description,
            "published_at": published_at,
            "source": source,
            "fetched_at": fetched_at,
            "media_urls": json.dumps(media_urls),
        },
    )
    conn.commit()


def fetch_and_ingest(feed_url: str, db_path: str, source: str) -> None:
    """Fetch a feed URL and store parsed entries."""

    logger.info("Fetching feed %s", feed_url)
    parsed = feedparser.parse(feed_url)

    if parsed.bozo:
        logger.error("Feed parsing encountered issues: %s", parsed.bozo_exception)

    if parsed.get("status") and parsed.status >= 400:
        logger.error("Feed returned HTTP %s", parsed.status)
        return

    entries: Iterable[Mapping[str, object]] = parsed.entries or []
    if not entries:
        logger.warning("No entries returned from feed %s", feed_url)
        return

    logger.info("Fetched %s entries from %s", len(parsed.entries), feed_url)

    conn = sqlite3.connect(db_path)
    ensure_tables(conn)

    ingested = 0
    for entry in entries:
        try:
            upsert_article(conn, entry, source)
            ingested += 1
        except sqlite3.DatabaseError as exc:
            logger.exception("Failed to upsert entry %s: %s", entry.get("id"), exc)

    logger.info("Ingested %s entries from %s", ingested, feed_url)
    conn.close()


def configure_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )


def run_schedule(feed_url: str, interval: int, db_path: str, source: str) -> None:
    logger.info(
        "Starting scheduled ingestion every %s seconds from %s", interval, feed_url
    )

    schedule.every(interval).seconds.do(fetch_and_ingest, feed_url, db_path, source)

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down scheduler")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RSS ingestion job")
    parser.add_argument("--feed-url", default=DEFAULT_FEED_URL, help="RSS feed URL")
    parser.add_argument(
        "--db-path",
        default=DEFAULT_DB_PATH,
        help="SQLite database path (default: %(default)s)",
    )
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE,
        help="Source identifier stored with records",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("INGEST_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)),
        help="Polling interval in seconds for scheduled mode",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single ingestion instead of scheduling",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)

    if args.once:
        fetch_and_ingest(args.feed_url, args.db_path, args.source)
        return 0

    run_schedule(args.feed_url, args.interval, args.db_path, args.source)
    return 0


if __name__ == "__main__":
    sys.exit(main())
