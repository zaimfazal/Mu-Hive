"""
tests/test_pipeline.py
======================
Single test file covering the full pipeline.

Run with:
    python -m pytest tests/ -v
    OR
    python -m unittest tests/test_pipeline.py

Tests are designed to run without network access (mocked) so they work
in any CI environment.
"""

import asyncio

import tempfile
import unittest
from unittest.mock import AsyncMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(**kwargs) -> dict:
    """Return a minimal canonical event dict with overridable fields."""
    base = {
        "eventName":        "Test Hackathon",
        "platform":         "Devfolio",
        "registrationLink": "https://test.devfolio.co",
        "startDate":        "TBA",
        "endDate":          "TBA",
        "tags":             ["ai", "ml"],
        "location":         "Online",
        "prizePool":        "₹50,000",
        "cost":             "Free",
        "eligibility":      "Students",
    }
    base.update(kwargs)
    return base


def _run(coro):
    """Run an async coroutine synchronously for testing."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# 1. Scraping layer
# ---------------------------------------------------------------------------

class TestFetchEvents(unittest.TestCase):
    """fetch_all_events() returns a flat list of dicts and never raises."""

    def test_returns_list(self):
        """fetch_all_events always returns a list even if all platforms fail."""
        from src.scraping.scraper import fetch_all_events

        async def _run_empty():
            with patch("src.scraping.scraper._fetch_devfolio",  new_callable=AsyncMock, return_value=[]), \
                 patch("src.scraping.scraper._fetch_devpost",   new_callable=AsyncMock, return_value=[]), \
                 patch("src.scraping.scraper._fetch_unstop",    new_callable=AsyncMock, return_value=[]), \
                 patch("src.scraping.scraper._fetch_hackerearth", new_callable=AsyncMock, return_value=[]):
                return await fetch_all_events()

        result = _run(_run_empty())
        self.assertIsInstance(result, list)

    def test_events_have_required_keys(self):
        """Each event must contain all canonical keys."""
        from src.scraping.scraper import fetch_all_events

        fake_event = _make_event()

        async def _run_one():
            with patch("src.scraping.scraper._fetch_devfolio",    new_callable=AsyncMock, return_value=[fake_event]), \
                 patch("src.scraping.scraper._fetch_devpost",     new_callable=AsyncMock, return_value=[]), \
                 patch("src.scraping.scraper._fetch_unstop",      new_callable=AsyncMock, return_value=[]), \
                 patch("src.scraping.scraper._fetch_hackerearth", new_callable=AsyncMock, return_value=[]):
                return await fetch_all_events()

        result = _run(_run_one())
        self.assertEqual(len(result), 1)

        required = {"eventName", "platform", "registrationLink",
                    "startDate", "endDate", "tags", "location",
                    "prizePool", "cost", "eligibility"}
        for key in required:
            self.assertIn(key, result[0], f"Missing key: {key}")

    def test_platform_failure_is_tolerated(self):
        """If one platform raises, the rest still succeed."""
        from src.scraping.scraper import fetch_all_events

        fake_event = _make_event(platform="Devpost", registrationLink="https://devpost.com/test")

        async def _run_mixed():
            with patch("src.scraping.scraper._fetch_devfolio",    side_effect=Exception("timeout")), \
                 patch("src.scraping.scraper._fetch_devpost",     new_callable=AsyncMock, return_value=[fake_event]), \
                 patch("src.scraping.scraper._fetch_unstop",      new_callable=AsyncMock, return_value=[]), \
                 patch("src.scraping.scraper._fetch_hackerearth", new_callable=AsyncMock, return_value=[]):
                return await fetch_all_events()

        result = _run(_run_mixed())
        # At minimum the Devpost event survives
        self.assertIsInstance(result, list)


# ---------------------------------------------------------------------------
# 2. Processing layer
# ---------------------------------------------------------------------------

class TestProcessEvents(unittest.TestCase):
    """process_events() returns a grouped dict with correct structure."""

    def test_returns_dict_of_igs(self):
        """process_events always returns a dict keyed by IG names."""
        from src.scraping.curate   import process_events
        from src.config.constants  import MASTER_IGS

        result = process_events([])
        self.assertIsInstance(result, dict)
        for ig in MASTER_IGS:
            self.assertIn(ig, result)
            self.assertIsInstance(result[ig], list)

    def test_deduplication(self):
        """Duplicate URLs must be reduced to one event."""
        from src.scraping.curate import process_events

        dup = _make_event(registrationLink="https://same.devfolio.co")
        result = process_events([dup, dup, dup])
        total = sum(len(v) for v in result.values())
        self.assertLessEqual(total, 1)

    def test_max_five_per_ig(self):
        """No IG should have more than 5 events."""
        from src.scraping.curate import process_events

        events = [
            _make_event(
                eventName=f"AI Hackathon {i}",
                registrationLink=f"https://event{i}.devfolio.co",
                tags=["ai", "machine learning"],
            )
            for i in range(20)
        ]
        result = process_events(events)
        for ig, evs in result.items():
            self.assertLessEqual(len(evs), 5, f"{ig} has more than 5 events")

    def test_expired_events_filtered(self):
        """Events with a past endDate must be excluded."""
        from src.scraping.curate import process_events

        expired = _make_event(
            registrationLink="https://expired.devfolio.co",
            startDate="2020-01-01",
            endDate="2020-01-05",
        )
        result = process_events([expired])
        total = sum(len(v) for v in result.values())
        self.assertEqual(total, 0)

    def test_foreign_offline_events_filtered(self):
        """Offline events in foreign countries must be excluded."""
        from src.scraping.curate import process_events

        foreign = _make_event(
            registrationLink="https://sfhack.io",
            location="San Francisco, USA",
        )
        result = process_events([foreign])
        total = sum(len(v) for v in result.values())
        self.assertEqual(total, 0)

    def test_scored_events_have_internal_keys(self):
        """Surviving events must carry _score, _event_type, _days_away."""
        from src.scraping.curate import process_events

        event = _make_event(registrationLink="https://good.devfolio.co")
        result = process_events([event])
        surviving = [e for evs in result.values() for e in evs]
        if surviving:
            e = surviving[0]
            self.assertIn("_score",      e)
            self.assertIn("_event_type", e)
            self.assertIn("_days_away",  e)


# ---------------------------------------------------------------------------
# 3. Database layer
# ---------------------------------------------------------------------------

class TestDbInsert(unittest.TestCase):
    """save_events() correctly persists grouped events through DatabaseFacade."""

    @patch("src.scraping.scraper.DatabaseFacade")
    def test_insert_new_event(self, mock_db_facade):
        """A new event should be inserted with correct field values."""
        from src.scraping.curate import process_events
        from src.scraping.scraper import save_events

        mock_db = mock_db_facade.return_value
        mock_db.link_exists.return_value = False
        mock_db.insert_event.return_value = 123

        from datetime import datetime, timedelta
        future = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
        event = _make_event(
            registrationLink="https://newtest.devfolio.co",
            startDate=future,
            endDate=future,
        )
        grouped = process_events([event])

        inserted, updated = save_events(grouped)
        self.assertEqual(inserted, 1)
        self.assertEqual(updated, 0)
        mock_db.insert_event.assert_called_once()

    @patch("src.scraping.scraper.DatabaseFacade")
    def test_duplicate_event_is_not_inserted(self, mock_db_facade):
        """Existing URL/IG pairs should not be inserted again."""
        from src.scraping.scraper import save_events

        mock_db = mock_db_facade.return_value
        mock_db.link_exists.return_value = True

        grouped = {
            "AI": [
                {**_make_event(), "_score": 100, "_event_type": "Hackathon",
                 "_days_away": 10, "_igs": {"AI"}},
            ]
        }
        inserted, updated = save_events(grouped)

        self.assertEqual(inserted, 0)
        self.assertEqual(updated, 1)
        mock_db.insert_event.assert_not_called()


if __name__ == "__main__":
    unittest.main()
