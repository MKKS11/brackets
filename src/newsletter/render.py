"""Render newsletter issues with HTML and plaintext templates.

This module assembles the top ranked articles into header, top stories,
more headlines, and footer sections. Links automatically receive UTM
parameters, and the rendered issue is persisted to a SQLite-backed
``newsletter_issues`` table for later inspection or delivery.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, List, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

DEFAULT_CAMPAIGN = "weekly_newsletter"
DEFAULT_DB_PATH = os.environ.get("NEWSLETTER_DB_PATH", "newsletter.db")


@dataclass(order=True)
class Article:
    """Represents a ranked article destined for the newsletter."""

    rank: int
    title: str
    url: str
    summary: str | None = None
    author: str | None = None
    tags: Sequence[str] | None = None


@dataclass
class NewsletterIssue:
    """Captured newsletter content for storage and delivery."""

    subject: str
    html_body: str
    text_body: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    articles: List[Article] = field(default_factory=list)
    pixels: Sequence[str] = field(default_factory=list)


def _apply_utm(url: str, campaign: str, source: str = "newsletter", medium: str = "email",
               extra_params: Mapping[str, str] | None = None) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query))
    query.update({
        "utm_source": source,
        "utm_medium": medium,
        "utm_campaign": campaign,
    })
    if extra_params:
        query.update(extra_params)
    encoded = urlencode(query, doseq=True)
    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        encoded,
        parsed.fragment,
    ))


def _render_header(subject: str, header_text: str | None) -> tuple[str, str]:
    html = f"<h1>{subject}</h1>"
    text = subject
    if header_text:
        html += f"\n<p>{header_text}</p>"
        text += f"\n\n{header_text}"
    return html, text


def _render_article_list(articles: Sequence[Article], campaign: str, section_title: str) -> tuple[str, str]:
    html_parts: List[str] = [f"<h2>{section_title}</h2>", "<ol>"]
    text_parts: List[str] = [section_title]
    for article in articles:
        link = _apply_utm(article.url, campaign=campaign)
        summary_html = f"<p>{article.summary}</p>" if article.summary else ""
        summary_text = f" - {article.summary}" if article.summary else ""
        html_parts.append(f"  <li><a href=\"{link}\">{article.title}</a>{summary_html}</li>")
        text_parts.append(f"* {article.title}: {link}{summary_text}")
    html_parts.append("</ol>")
    text_parts.append("")
    return "\n".join(html_parts), "\n".join(text_parts)


def _render_more_headlines(articles: Sequence[Article], campaign: str) -> tuple[str, str]:
    html_parts: List[str] = ["<h2>More headlines</h2>", "<ul>"]
    text_parts: List[str] = ["More headlines"]
    for article in articles:
        link = _apply_utm(article.url, campaign=campaign)
        html_parts.append(f"  <li><a href=\"{link}\">{article.title}</a></li>")
        text_parts.append(f"* {article.title}: {link}")
    html_parts.append("</ul>")
    text_parts.append("")
    return "\n".join(html_parts), "\n".join(text_parts)


def _render_footer(unsubscribe_url: str | None, pixels: Sequence[str]) -> tuple[str, str]:
    html_parts: List[str] = ["<hr/>"]
    text_parts: List[str] = []

    if unsubscribe_url:
        html_parts.append(f"<p><a href=\"{unsubscribe_url}\">Unsubscribe</a> if you no longer wish to receive these updates.</p>")
        text_parts.append(f"Unsubscribe: {unsubscribe_url}")

    for pixel in pixels:
        html_parts.append(
            f"<img src=\"{pixel}\" alt=\"tracking pixel\" width=\"1\" height=\"1\" style=\"display:none;\" />"
        )
    text_parts.append("")

    return "\n".join(html_parts), "\n".join(text_parts)


def render_issue(
    articles: Iterable[Article],
    subject: str,
    *,
    header_text: str | None = None,
    unsubscribe_url: str | None = None,
    analytics_pixels: Sequence[str] | None = None,
    campaign: str = DEFAULT_CAMPAIGN,
) -> NewsletterIssue:
    """Render HTML and plaintext versions of the newsletter.

    The function limits the provided articles to the top 15 based on ``rank`` and
    splits them into a top-stories block (first 5) and a more-headlines block
    (remaining articles).
    """

    sorted_articles = sorted(articles)[:15]
    if not sorted_articles:
        raise ValueError("At least one article is required to render the newsletter.")

    top_stories = sorted_articles[:5]
    more_headlines = sorted_articles[5:]
    pixels: Sequence[str] = analytics_pixels or []

    header_html, header_text_body = _render_header(subject, header_text)
    top_html, top_text = _render_article_list(top_stories, campaign, "Top stories")
    more_html, more_text = ("", "")
    if more_headlines:
        more_html, more_text = _render_more_headlines(more_headlines, campaign)

    footer_html, footer_text = _render_footer(unsubscribe_url, pixels)

    html_body = "\n".join(filter(None, [header_html, top_html, more_html, footer_html]))
    text_body = "\n".join(filter(None, [header_text_body, top_text, more_text, footer_text]))

    return NewsletterIssue(
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        articles=list(sorted_articles),
        pixels=list(pixels),
    )


def save_issue(issue: NewsletterIssue, *, db_path: str = DEFAULT_DB_PATH) -> int:
    """Persist the rendered issue into a SQLite ``newsletter_issues`` table.

    The table schema keeps the rendered bodies alongside the serialized article
    metadata to make future replays or audits straightforward.
    """

    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS newsletter_issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                subject TEXT NOT NULL,
                html_body TEXT NOT NULL,
                text_body TEXT NOT NULL,
                articles_json TEXT NOT NULL,
                pixels_json TEXT NOT NULL
            )
            """
        )
        cursor = connection.execute(
            """
            INSERT INTO newsletter_issues (
                created_at, subject, html_body, text_body, articles_json, pixels_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                issue.created_at.isoformat(),
                issue.subject,
                issue.html_body,
                issue.text_body,
                json.dumps([article.__dict__ for article in issue.articles]),
                json.dumps(list(issue.pixels)),
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)
    finally:
        connection.close()


__all__ = [
    "Article",
    "NewsletterIssue",
    "render_issue",
    "save_issue",
]
