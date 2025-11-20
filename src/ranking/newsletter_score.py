"""Scoring utilities for newsletter article selection.

The scoring function weights recency, source reputation, engagement metrics
(click/open rates) when available, and optional editorial boosts. A daily ranker
filters out low-quality articles, assigns newsletter ranks, and persists a JSON
artifact for downstream consumption.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, date, timezone
from typing import Iterable, List, Optional


@dataclass
class ArticleMetrics:
    """Container for newsletter ranking inputs and outputs."""

    article_id: str
    published_at: datetime
    source_weight: float = 1.0
    click_rate: Optional[float] = None
    open_rate: Optional[float] = None
    editorial_boost: float = 0.0
    quality_score: float = 1.0
    newsletter_rank: Optional[int] = field(default=None, init=False)
    score: Optional[float] = field(default=None, init=False)

    def __post_init__(self) -> None:
        if isinstance(self.published_at, date) and not isinstance(
            self.published_at, datetime
        ):
            # Promote date to midnight-aware datetime for consistent math.
            self.published_at = datetime.combine(
                self.published_at, datetime.min.time(), tzinfo=timezone.utc
            )
        elif self.published_at.tzinfo is None:
            self.published_at = self.published_at.replace(tzinfo=timezone.utc)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def _recency_weight(published_at: datetime, as_of: datetime, half_life_days: float) -> float:
    age_seconds = max((as_of - published_at).total_seconds(), 0)
    age_days = age_seconds / 86400.0
    decay = math.exp(-math.log(2) * age_days / half_life_days)
    return _clamp(decay, 0.0, 1.0)


def _engagement_score(click_rate: Optional[float], open_rate: Optional[float]) -> float:
    weighted = 0.0
    weight_total = 0.0

    if click_rate is not None:
        weighted += _clamp(click_rate, 0.0, 1.0) * 0.6
        weight_total += 0.6
    if open_rate is not None:
        weighted += _clamp(open_rate, 0.0, 1.0) * 0.4
        weight_total += 0.4

    # Default to a modest baseline engagement when data is absent.
    if weight_total == 0:
        return 0.35

    return weighted / weight_total


def compute_newsletter_score(
    article: ArticleMetrics,
    as_of: Optional[datetime] = None,
    *,
    recency_half_life_days: float = 3.0,
) -> float:
    """Compute the newsletter score for a single article.

    Components:
    - Recency with exponential decay.
    - Source weight normalized to 0..1 range.
    - Engagement from click/open rates when present.
    - Quality score multiplier and editorial boost.
    """

    as_of = as_of or datetime.now(tz=timezone.utc)

    recency = _recency_weight(article.published_at, as_of, recency_half_life_days)
    source_strength = _clamp(article.source_weight / 3.0, 0.0, 1.0)
    engagement = _engagement_score(article.click_rate, article.open_rate)
    quality_factor = _clamp(article.quality_score, 0.0, 1.0)
    boost = _clamp(article.editorial_boost, -0.5, 1.5)

    base_score = (
        recency * 0.4
        + source_strength * 0.2
        + engagement * 0.25
        + quality_factor * 0.15
    )

    score = base_score + boost * 0.3
    return round(score, 6)


def rank_articles(
    articles: Iterable[ArticleMetrics],
    *,
    as_of: Optional[datetime] = None,
    limit: int = 15,
    min_quality: float = 0.4,
    recency_half_life_days: float = 3.0,
) -> List[ArticleMetrics]:
    """Score articles, filter by quality, and assign newsletter ranks."""

    as_of = as_of or datetime.now(tz=timezone.utc)
    ranked: List[ArticleMetrics] = []

    for article in articles:
        if article.quality_score < min_quality:
            article.newsletter_rank = None
            article.score = None
            continue

        article.score = compute_newsletter_score(
            article, as_of=as_of, recency_half_life_days=recency_half_life_days
        )
        ranked.append(article)

    ranked.sort(key=lambda art: art.score or 0, reverse=True)

    for idx, article in enumerate(ranked, start=1):
        if idx > limit:
            article.newsletter_rank = None
            continue
        article.newsletter_rank = idx

    return ranked[:limit]


def persist_daily_newsletter_ranks(
    articles: Iterable[ArticleMetrics],
    output_dir: str,
    *,
    as_of: Optional[datetime] = None,
    limit: int = 15,
    min_quality: float = 0.4,
    recency_half_life_days: float = 3.0,
) -> str:
    """Persist top-ranked articles for a given day to a JSON artifact."""

    as_of = as_of or datetime.now(tz=timezone.utc)
    selected = rank_articles(
        articles,
        as_of=as_of,
        limit=limit,
        min_quality=min_quality,
        recency_half_life_days=recency_half_life_days,
    )

    os.makedirs(output_dir, exist_ok=True)
    date_key = as_of.date().isoformat()
    output_path = os.path.join(output_dir, f"{date_key}_newsletter_ranks.json")

    serializable = [
        {
            "article_id": article.article_id,
            "newsletter_rank": article.newsletter_rank,
            "score": article.score,
            "quality_score": article.quality_score,
            "published_at": article.published_at.isoformat(),
        }
        for article in selected
        if article.newsletter_rank is not None
    ]

    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "as_of": as_of.isoformat(),
                "limit": limit,
                "min_quality": min_quality,
                "articles": serializable,
            },
            handle,
            indent=2,
            sort_keys=True,
        )

    return output_path
