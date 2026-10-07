"""tests/test_phase7.py
======================
Phase 7 production-readiness (mocked, offline).

Covers: race-safe upsert delegation (ON CONFLICT), verify_setup opt-in
seeding, llm_retries wiring, split/hard-fail counters, hard-fail abort
threshold, quota watermark, search provider counters, search dry-run, and
the gmail stats-contract fix. No network, DB, sends, or live search.
"""

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("GROQ_API_KEY", "test-dummy-key")


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _make_doc(i, **kw):
    doc = {
        "_id": i, "title": f"Plain test article {i}",
        "summary": f"Plain content {i}", "scraped_full_text": None,
        "category": "Unknown", "ig_tags": ["AI"],
        "url": "https://example.com/plain", "link": f"https://example.com/{i}",
        "source": "", "data": {},
    }
    doc.update(kw)
    return doc


def _make_eval(item_id, **kw):
    from src.agents.intelligence import BatchedOpportunityIntelligence

    base = {
        "item_id": str(item_id), "is_relevant": True, "quality_score": 7,
        "reasoning": "Good.", "ig_tags": ["AI"], "category": "News",
        "summary": "Summary.", "structured_metadata": None,
    }
    base.update(kw)
    return BatchedOpportunityIntelligence(**base)


def _batch_result(evals):
    from src.agents.intelligence import IntelligenceBatchResult

    return SimpleNamespace(output=IntelligenceBatchResult(evaluations=evals))


def _run_intelligence(docs, side_effect, batch_size=8):
    from src.agents import intelligence as intel

    with patch.object(intel, "Database") as mock_db_cls, \
         patch.object(intel.batch_intelligence_agent, "run", new_callable=AsyncMock) as mock_run, \
         patch.object(intel.asyncio, "sleep", new_callable=AsyncMock):
        mock_db = mock_db_cls.return_value
        mock_db.get_unprocessed_for_intelligence.return_value = docs
        mock_run.side_effect = side_effect
        stats = _run(intel.run_intelligence(batch_limit=len(docs), batch_size=batch_size))
    return stats, mock_db, mock_run


class TestRaceSafeUpsert(unittest.TestCase):
    def test_upsert_uses_on_conflict(self):
        from tests.test_event_igs import FakeState, _patched_state
        from src.db import orchestrator_writer as writer

        state = FakeState()
        with _patched_state(state):
            eid = writer._upsert_event_without_constraint(
                title="T", ig="AI", summary="S",
                apply_link="https://x/race", validity_score=7)
            again = writer._upsert_event_without_constraint(
                title="T2", ig="AI", summary="S2",
                apply_link="https://x/race", validity_score=8)
        # Concurrent-sequential writers converge on one canonical row...
        self.assertEqual(eid, again)
        self.assertEqual(len(state.events), 1)
        # ...with conflict-update semantics applied...
        self.assertEqual(state.events[eid]["title"], "T2")
        self.assertEqual(state.events[eid]["validity_score"], 8)
        # ...a single junction membership, and ON CONFLICT in the SQL.
        self.assertEqual(state.igs, {(eid, "AI")})
        upserts = [sql for sql, _ in state.statements
                   if sql.strip().startswith("INSERT INTO events")]
        self.assertTrue(upserts)
        self.assertTrue(all("ON CONFLICT (apply_link)" in sql for sql in upserts))


class TestVerifySetupSafety(unittest.TestCase):
    def _run_test_db(self, env_extra=None):
        import scripts.verify_setup as vs

        executed = []

        class FakeCur:
            def __init__(self):
                self._queue = []
                self.results = {
                    "scraped_data": [(True,)], "events": [(True,)],
                    "zulip_sent": [(True,)], "ig_mails": [(True,)],
                }

            def execute(self, sql, params=None):
                executed.append((" ".join(sql.split()), params))
                s = " ".join(sql.split())
                if "information_schema.tables" in s:
                    self._queue.append((True,))
                elif "information_schema.columns" in s:
                    self._queue.append((True,))
                elif sql.strip().startswith("SELECT ig FROM ig_mails"):
                    self._queue.append(None)  # end of rows
                elif "SELECT 1 FROM ig_mails" in s:
                    self._queue.append(None)  # missing mapping
                elif "COUNT(*)" in s:
                    self._queue.append((0,))

            def fetchone(self):
                return self._queue.pop(0) if self._queue else None

            def fetchall(self):
                rows = []
                while self._queue:
                    row = self._queue.pop(0)
                    if row is None:
                        break
                    rows.append(row)
                return rows

            def close(self):
                pass

        class FakeConn:
            def __init__(self):
                self.cur = FakeCur()
                self.autocommit = True

            def cursor(self):
                return self.cur

            def close(self):
                pass

        env = {"DATABASE_URL": "postgresql://test/test", "GROQ_API_KEY": "x"}
        env.update(env_extra or {})
        with patch("scripts.verify_setup.psycopg2") as mock_pg, \
             patch.dict(os.environ, env, clear=True):
            mock_pg.connect.return_value = FakeConn()
            ok = vs.test_db()
        return ok, executed

    def test_no_seed_by_default(self):
        ok, executed = self._run_test_db()
        self.assertTrue(ok)
        inserts = [sql for sql, _ in executed
                   if sql.startswith("INSERT INTO ig_mails")]
        self.assertEqual(inserts, [])  # validation only, no prod writes

    def test_opt_in_seed_writes(self):
        ok, executed = self._run_test_db(
            {"SEED_DEFAULT_IG_MAILS": "true"})
        self.assertTrue(ok)
        inserts = [sql for sql, _ in executed
                   if sql.startswith("INSERT INTO ig_mails")]
        self.assertEqual(len(inserts), 3)


class TestRetryObservability(unittest.TestCase):
    def test_llm_retries_wired(self):
        docs = [_make_doc(1)]
        good = _batch_result([_make_eval(1)])
        err = Exception("LLM API returned 429. Please try again in 1s")
        stats, _, mock_run = _run_intelligence(docs, [err, good])
        self.assertEqual(mock_run.call_count, 2)
        self.assertEqual(stats["llm_retries"], 1)
        self.assertEqual(stats["total_evaluated"], 1)

    def test_no_retry_no_count(self):
        docs = [_make_doc(1)]
        stats, _, _ = _run_intelligence(docs, [_batch_result([_make_eval(1)])])
        self.assertEqual(stats["llm_retries"], 0)

    def test_splits_counted(self):
        docs = [_make_doc(i) for i in range(1, 5)]
        stats, mock_db, mock_run = _run_intelligence(docs, Exception("boom"))
        self.assertEqual(mock_run.call_count, 7)  # 1 + 2 + 4
        self.assertEqual(stats["splits"], 3)
        self.assertEqual(stats.get("failed"), 4)
        mock_db.update_intelligence.assert_not_called()

    def test_hard_fail_count_and_abort(self):
        from src.agents.intelligence import LLMFailureThresholdExceeded

        docs = [_make_doc(1)]
        stats, _, _ = _run_intelligence(docs, Exception("400 bad request"))
        self.assertEqual(stats["hard_fails"], 1)
        self.assertEqual(stats.get("failed"), 1)

        docs11 = [_make_doc(i) for i in range(1, 12)]
        with self.assertRaises(LLMFailureThresholdExceeded):
            _run_intelligence(docs11, Exception("400 bad request"),
                              batch_size=1)
        # 11th hard fail trips the threshold (count observed via raise).

    def test_quota_watermark_reported(self):
        from src.utils import groq_quota

        old = groq_quota._groq_tokens_used
        groq_quota._groq_tokens_used = 50000
        try:
            docs = [_make_doc(1)]
            stats, _, _ = _run_intelligence(docs, [_batch_result([_make_eval(1)])])
            self.assertEqual(stats["quota_used_high_watermark"], 50000)
        finally:
            groq_quota._groq_tokens_used = old


class TestSearchCounters(unittest.TestCase):
    def _stats(self, texts, max_results=3):
        from src.scraping import search_engine as se

        mock_db = MagicMock()
        mock_db.link_exists.return_value = False
        mock_db.insert_event.return_value = 1
        mock_ddgs = MagicMock()
        mock_ddgs.return_value.text.side_effect = texts
        index = {"q1": [("AI", "news")], "q2": [("AI", "news")]}
        with patch("ddgs.DDGS", mock_ddgs):
            with patch.object(se.time, "sleep"):
                return se.run_registry_search(mock_db, index, max_results)

    def test_ddg_success_no_tavily(self):
        stats = self._stats([[ {"title": "T", "href": "https://x/1"} ],
                             [ {"title": "T2", "href": "https://x/2"} ]])
        self.assertEqual(stats["provider_calls"], {"ddg": 2, "tavily": 0})
        self.assertEqual(stats["tavily_fallbacks"], 0)
        self.assertEqual(stats["queries_run"], 2)

    def test_ddg_failure_falls_back(self):
        from src.scraping import search_engine as se

        mock_db = MagicMock()
        mock_db.link_exists.return_value = False
        mock_db.insert_event.return_value = 1
        mock_ddgs = MagicMock()
        mock_ddgs.return_value.text.side_effect = Exception("ddg down")
        index = {"q1": [("AI", "news")]}
        with patch("ddgs.DDGS", mock_ddgs), \
             patch.object(se, "get_tavily_results", return_value=[]) as mock_tv, \
             patch.object(se.time, "sleep"):
            stats = se.run_registry_search(mock_db, index, max_results=3)
        self.assertEqual(stats["provider_calls"]["ddg"], 0)
        self.assertEqual(stats["provider_calls"]["tavily"], 1)
        self.assertEqual(stats["tavily_fallbacks"], 1)
        mock_tv.assert_called_once()


class TestSearchDryRun(unittest.TestCase):
    def test_dry_run_counts_no_network(self):
        from src.scraping.search_engine import build_search_dry_run
        from src.config.interest_groups import registry
        from src.config import settings

        with patch("ddgs.DDGS", side_effect=AssertionError("no network")):
            report = build_search_dry_run(
                registry, settings.DISCOVERY_MAX_QUERIES_PER_IG,
                settings.INTELLIGENCE_MAX_DOCS_PER_RUN,
                settings.SEARCH_ENABLED)
        self.assertIs(report["search_enabled"], False)
        self.assertEqual(report["igs_configured"], 30)
        self.assertEqual(report["igs_ready"], 30)
        self.assertEqual(report["igs_without_queries"], [])
        self.assertGreater(report["unique_queries"], 0)
        self.assertEqual(report["expected_query_count"], report["unique_queries"])
        self.assertEqual(report["max_docs_to_intelligence"], 30)
        self.assertEqual(len(report["igs_without_destinations"]), 13)


class TestGmailStatsContract(unittest.TestCase):
    def test_missing_creds_returns_zeroed_stats(self):
        import scripts.gmailsender as gm

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GMAIL_SENDER", None)
            os.environ.pop("GMAIL_APP_PASSWORD", None)
            stats = gm.run_email_agent()
        self.assertEqual(stats, {
            "igs_configured": 0, "igs_with_recipients": 0,
            "igs_processed": 0, "igs_skipped_cap": 0,
            "igs_unconfigured": 0, "emails_sent": 0,
        })


if __name__ == "__main__":
    unittest.main()
