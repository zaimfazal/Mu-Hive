"""tests/test_orchestrator_writer.py
=====================================
Deterministic hackathon summaries for ordinary ingestion.

- Summary formatting includes available metadata.
- Missing/placeholder fields are omitted (never "None"/"Unknown"/empty labels).
- Ordinary hackathon ingestion does not call the LLM summarizer (mocked).

Offline: DB and LLM are mocked; no provider is contacted.
"""

import asyncio
import os

# Must be set before importing src.agents.summarizer (agent_config exits
# without a key). Dummy value only; the tests assert the LLM is never called.
os.environ.setdefault("GROQ_API_KEY", "test-dummy-key")

import unittest
from unittest.mock import AsyncMock, patch


def _run(coro):
    return asyncio.run(coro)


class TestFallbackSummary(unittest.TestCase):
    def test_full_metadata_included(self):
        from src.db.orchestrator_writer import _fallback_summary

        summary = _fallback_summary(
            "AI Hackathon",
            "AI",
            "Devfolio",
            "Online",
            3,
            description="A 48-hour build sprint for ML hackers.",
            deadline="2026-10-10",
            prize="₹50,000",
        )
        self.assertIsInstance(summary, str)
        for token in [
            "AI Hackathon",
            "AI-relevant hackathon",
            "Devfolio",
            "Online",
            "3 day(s) left",
            "2026-10-10",
            "₹50,000",
            "48-hour build sprint",
        ]:
            self.assertIn(token, summary)

    def test_missing_fields_omitted(self):
        from src.db.orchestrator_writer import _fallback_summary

        summary = _fallback_summary(
            "AI Hackathon",
            "AI",
            None,
            "",
            None,
            description=None,
            deadline="TBA",
            prize="",
        )
        self.assertIsInstance(summary, str)
        self.assertIn("AI Hackathon", summary)
        for banned in ["None", "Unknown", "N/A", "TBA", "  "]:
            self.assertNotIn(banned, summary)
        self.assertNotIn(" on ", summary)
        self.assertNotIn("hosted", summary)
        self.assertNotIn("prize", summary)
        self.assertNotIn("deadline", summary)

    def test_placeholder_title_not_rendered(self):
        from src.db.orchestrator_writer import _fallback_summary

        summary = _fallback_summary("Unknown", "AI", "Devfolio", "Online", 5)
        self.assertNotIn("Unknown", summary)
        self.assertIn("AI-relevant hackathon", summary)


class TestIngestionUsesNoLLM(unittest.TestCase):
    def _run_ingestion(self, events):
        from src.db import orchestrator_writer as writer

        with patch.object(writer, "_ensure_events_schema"), \
             patch.object(writer, "_upsert_event_without_constraint", return_value=1) as mock_upsert, \
             patch.object(writer, "DatabaseFacade") as mock_db_cls:
            mock_db = mock_db_cls.return_value
            mock_db.insert_opportunity.return_value = True
            result = _run(writer.save_orchestrator_events({"AI": events}))
        return result, mock_db, mock_upsert

    def test_ingestion_does_not_call_summarizer(self):
        import src.agents.summarizer as summarizer_module
        from src.db import orchestrator_writer as writer

        event = {
            "eventName": "AI Hackathon",
            "platform": "Devfolio",
            "registrationLink": "https://test.devfolio.co/no-llm",
            "startDate": "2026-10-01",
            "endDate": "2026-10-10",
            "tags": ["ai", "ml"],
            "location": "Online",
            "prizePool": "₹50,000",
            "cost": "Free",
            "eligibility": "Students",
            "description": "A 48-hour build sprint.",
        }
        with patch.object(
            summarizer_module.summarizer_agent, "run",
            new_callable=AsyncMock,
            side_effect=AssertionError("LLM summarizer must not be called"),
        ) as mock_run, \
            patch.object(writer, "_ensure_events_schema"), \
            patch.object(writer, "_upsert_event_without_constraint", return_value=1), \
            patch.object(writer, "DatabaseFacade") as mock_db_cls:
            mock_db = mock_db_cls.return_value
            mock_db.insert_opportunity.return_value = True
            result = _run(writer.save_orchestrator_events({"AI": [event]}))

        mock_run.assert_not_called()
        self.assertEqual(result, {"inserted_scraped": 1, "upserted_events": 1})
        # Summary persisted as a deterministic non-empty string.
        _, kwargs = mock_db.insert_opportunity.call_args
        summary = kwargs.get("summary") or mock_db.insert_opportunity.call_args[0]
        self.assertIsInstance(kwargs["summary"], str)
        self.assertTrue(kwargs["summary"])
        expected = writer._fallback_summary(
            "AI Hackathon",
            "AI",
            "Devfolio",
            "Online",
            None,
            description="A 48-hour build sprint.",
            deadline="2026-10-10",
            prize="₹50,000",
        )
        self.assertEqual(kwargs["summary"], expected)

    def test_ingestion_summary_omits_missing_fields(self):
        from src.db import orchestrator_writer as writer  # noqa: F401

        event = {
            "eventName": "Web Hackathon",
            "platform": None,
            "registrationLink": "https://test.devfolio.co/minimal",
            "startDate": "TBA",
            "endDate": "TBA",
            "tags": [],
            "location": None,
            "prizePool": "",
            "cost": "Free",
            "eligibility": "Students",
        }
        result, mock_db, _ = self._run_ingestion([event])
        self.assertEqual(result["upserted_events"], 1)
        _, kwargs = mock_db.insert_opportunity.call_args
        summary = kwargs["summary"]
        self.assertIsInstance(summary, str)
        for banned in ["None", "Unknown", "N/A", "TBA"]:
            self.assertNotIn(banned, summary)


if __name__ == "__main__":
    unittest.main()
