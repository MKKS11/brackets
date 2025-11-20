import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from src.ranking.newsletter_score import (
    ArticleMetrics,
    compute_newsletter_score,
    persist_daily_newsletter_ranks,
    rank_articles,
)


class NewsletterScoreTests(unittest.TestCase):
    def test_score_rewards_recency_and_boosts(self):
        now = datetime.now(tz=timezone.utc)
        recent = ArticleMetrics(
            article_id="recent",
            published_at=now - timedelta(hours=6),
            source_weight=1.0,
            quality_score=0.9,
        )
        older = ArticleMetrics(
            article_id="older",
            published_at=now - timedelta(days=2),
            source_weight=1.2,
            editorial_boost=0.2,
        )

        recent_score = compute_newsletter_score(recent, as_of=now)
        older_score = compute_newsletter_score(older, as_of=now)

        self.assertGreater(recent_score, older_score)

        boosted = ArticleMetrics(
            article_id="boosted",
            published_at=now - timedelta(days=1),
            source_weight=1.0,
            editorial_boost=0.8,
            quality_score=0.9,
        )
        boosted_score = compute_newsletter_score(boosted, as_of=now)
        self.assertGreater(boosted_score, recent_score)

    def test_rank_filters_quality_and_limit(self):
        now = datetime.now(tz=timezone.utc)
        articles = [
            ArticleMetrics(
                article_id=f"a{i}",
                published_at=now - timedelta(hours=i),
                quality_score=0.9 if i % 2 == 0 else 0.3,
                source_weight=1 + i * 0.1,
                click_rate=0.1 * i,
            )
            for i in range(20)
        ]

        ranked = rank_articles(articles, as_of=now, limit=15, min_quality=0.5)

        self.assertEqual(len(ranked), 10)  # half of the articles are below the threshold
        self.assertTrue(all(article.newsletter_rank is not None for article in ranked))
        self.assertEqual(ranked[0].newsletter_rank, 1)
        self.assertEqual(ranked[-1].newsletter_rank, len(ranked))

    def test_persist_daily_newsletter_ranks(self):
        now = datetime(2023, 1, 5, tzinfo=timezone.utc)
        articles = [
            ArticleMetrics(
                article_id="persisted",
                published_at=now - timedelta(hours=4),
                click_rate=0.5,
                open_rate=0.6,
                quality_score=0.8,
            )
        ]

        with tempfile.TemporaryDirectory() as directory:
            output_path = persist_daily_newsletter_ranks(
                articles, directory, as_of=now, limit=15, min_quality=0.5
            )
            self.assertTrue(os.path.exists(output_path))

            with open(output_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)

            self.assertEqual(payload["min_quality"], 0.5)
            self.assertEqual(payload["limit"], 15)
            self.assertEqual(payload["articles"][0]["article_id"], "persisted")
            self.assertEqual(payload["articles"][0]["newsletter_rank"], 1)
            self.assertIsNotNone(payload["articles"][0]["score"])


if __name__ == "__main__":
    unittest.main()
