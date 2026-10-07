"""tests/test_intelligence_igs30.py
==================================
Phase 5 registry-wide classification (mocked, offline).

Covers gaps left by test_intelligence_batch.py (per-doc batch basics),
test_registry_rollout.py (enumeration, search plan) and test_event_igs.py
(persistence): 30-name classification sweep, aliases/case/invalid names,
multi-group stats, score-component matrix, validation unit matrix,
no-split guarantees, and default pins. No network, DB, or LLM calls.
"""

import asyncio
import inspect
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

# Must be set before importing src.agents.intelligence (agent_config exits
# without a key). Dummy value only; the LLM is always mocked below.
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
        "_id": i,
        "title": f"Plain test article {i}",
        "summary": f"Plain content {i}",
        "scraped_full_text": None,
        "category": "Unknown",
        "ig_tags": ["AI"],
        "url": "https://example.com/plain",
        "link": f"https://example.com/{i}",
        "source": "",
        "data": {},
    }
    doc.update(kw)
    return doc


def _make_eval(item_id, **kw):
    from src.agents.intelligence import BatchedOpportunityIntelligence

    base = {
        "item_id": str(item_id),
        "is_relevant": True,
        "quality_score": 7,
        "reasoning": "Good.",
        "ig_tags": ["AI"],
        "category": "News",
        "summary": "Summary.",
        "structured_metadata": None,
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


class TestThirtyIGSweep(unittest.TestCase):
    def test_all_30_names_classify_and_normalize(self):
        from src.config.interest_groups import registry

        names = registry.all_active_names()
        self.assertEqual(len(names), 30)
        docs = [_make_doc(i, ig_tags=[name])
                for i, name in enumerate(names, start=1)]
        evals = [_make_eval(i, ig_tags=[name], quality_score=7)
                 for i, name in enumerate(names, start=1)]
        stats, mock_db, _ = _run_intelligence(docs, [_batch_result(evals)],
                                              batch_size=30)
        by_id = {}
        for c in mock_db.update_intelligence.call_args_list:
            by_id[c.args[0]] = c.args[2]
        for i, name in enumerate(names, start=1):
            self.assertEqual(by_id[i], [name], f"IG {name}")
            self.assertEqual(stats["by_ig"].get(name), 1, f"stats {name}")
        self.assertEqual(stats["total_evaluated"], 30)

    def test_registry_normalizer_agreement(self):
        from src.config.interest_groups import registry
        from src.agents.intelligence import _normalize_ig

        for name in registry.all_active_names():
            self.assertEqual(registry.normalize(name), name)
            self.assertEqual(_normalize_ig(name), name)

    def test_prompt_covers_registry(self):
        from src.agents import intelligence as intel
        from src.config.interest_groups import registry

        prompt = intel.INTELLIGENCE_SYSTEM_PROMPT
        for name in registry.all_active_names():
            if name == "General Tech":
                continue  # registry itself excludes it from definitions
            self.assertIn(f"**{name}**", prompt, name)
        for rule in ("Malware/trojans/phishing/breaches",
                     "Cybersecurity tools using ML internally",
                     "Hardware/semiconductor news",
                     "Physical product design"):
            self.assertIn(rule, prompt)
        self.assertIn("## Evidence", prompt)


class TestAliasesAndInvalid(unittest.TestCase):
    def test_alias_sweep_from_yaml(self):
        from src.config.interest_groups import registry

        checked = 0
        for ig in registry.all_active():
            for alias in ig.aliases:
                self.assertEqual(registry.normalize(alias), ig.name, alias)
                checked += 1
        self.assertGreater(checked, 30)

    def test_case_whitespace_robustness(self):
        docs = [_make_doc(1), _make_doc(2), _make_doc(3)]
        evals = [_make_eval(1, ig_tags=["  ai  "]),
                 _make_eval(2, ig_tags=["CYBER SECURITY"]),
                 _make_eval(3, ig_tags=["Ui/Ux "])]
        _, mock_db, _ = _run_intelligence(docs, [_batch_result(evals)])
        by_id = {c.args[0]: c.args[2]
                 for c in mock_db.update_intelligence.call_args_list}
        self.assertEqual(by_id[1], ["AI"])
        self.assertEqual(by_id[2], ["Cyber Security"])
        self.assertEqual(by_id[3], ["UI/UX"])

    def test_invalid_tags_skip_without_evaluation(self):
        docs = [_make_doc(1, ig_tags=[]), _make_doc(2, ig_tags=[]),
                _make_doc(3, ig_tags=[]), _make_doc(4, ig_tags=[])]
        evals = [_make_eval(1, ig_tags=[]),
                 _make_eval(2, ig_tags=["Unknown"]),
                 _make_eval(3, ig_tags=["not-a-real-ig"]),
                 _make_eval(4, ig_tags=["   "])]
        stats, mock_db, _ = _run_intelligence(docs, [_batch_result(evals)])
        for c in mock_db.update_intelligence.call_args_list:
            self.assertEqual((c.args[1], c.args[2]), (0, []))
        self.assertEqual(stats["skipped_invalid_ig"], 4)
        self.assertEqual(stats["total_evaluated"], 0)  # never a valid evaluation
        mock_db.update_event_summary_by_link.assert_not_called()

    def test_source_fallback_rescue_and_count(self):
        docs = [_make_doc(1, ig_tags=["Space"]),
                _make_doc(2, ig_tags=["bogus", " "])]
        evals = [_make_eval(1, ig_tags=["bogus"]), _make_eval(2, ig_tags=["bogus"])]
        stats, mock_db, _ = _run_intelligence(docs, [_batch_result(evals)])
        by_id = {c.args[0]: c.args[2]
                 for c in mock_db.update_intelligence.call_args_list}
        self.assertEqual(by_id[1], ["Space"])  # rescued via source hint
        self.assertEqual(stats["fallback_used"], 1)
        self.assertEqual(stats["skipped_invalid_ig"], 1)  # doc 2 unrescuable


class TestMultiGroupStats(unittest.TestCase):
    def test_multi_group_eval_stats(self):
        docs = [_make_doc(1)]
        evals = [_make_eval(1, ig_tags=["Web Development", "AI", "Data Science"])]
        stats, mock_db, _ = _run_intelligence(docs, [_batch_result(evals)])
        c = mock_db.update_intelligence.call_args
        # Primary order preserved for legacy events.ig = tags[0].
        self.assertEqual(c.args[2], ["Web Development", "AI", "Data Science"])
        for tag in ("Web Development", "AI", "Data Science"):
            self.assertEqual(stats["by_ig"].get(tag), 1)
        self.assertEqual(stats["ig_assignments"], 3)
        self.assertEqual(stats["membership_histogram"], {"3": 1})


class TestScoreMatrix(unittest.TestCase):
    def _score_of(self, source, **doc_kw):
        docs = [_make_doc(1, source=source, **doc_kw)]
        evals = [_make_eval(1, quality_score=7)]
        _, mock_db, _ = _run_intelligence(docs, [_batch_result(evals)])
        c = mock_db.update_intelligence.call_args
        return c.args[1], c.kwargs["score_breakdown"]

    def test_source_boost_matrix(self):
        expected = {
            "RSS": (9, "source=2"), "API": (8, "source=1"),
            "Firecrawl": (8, "source=1"), "Tavily": (7, "source=0"),
            "DuckDuckGo": (7, "source=0"), "": (7, "source=0"),
        }
        for source, (score, fragment) in expected.items():
            with self.subTest(source=source):
                got, breakdown = self._score_of(source)
                self.assertEqual(got, score)
                self.assertIn(fragment, breakdown)

    def test_threshold_rules(self):
        docs = [_make_doc(1), _make_doc(2, source="RSS"),
                _make_doc(3, source="RSS"), _make_doc(4)]
        evals = [_make_eval(1, quality_score=5),
                 _make_eval(2, quality_score=6),
                 _make_eval(3, quality_score=9),
                 _make_eval(4, is_relevant=False, quality_score=3)]
        stats, mock_db, _ = _run_intelligence(docs, [_batch_result(evals)])
        by_id = {}
        breakdowns = {}
        for c in mock_db.update_intelligence.call_args_list:
            by_id[c.args[0]] = c.args[1]
            breakdowns[c.args[0]] = c.kwargs["score_breakdown"]
        self.assertEqual(by_id[1], 5)  # no bonus below 6
        self.assertIn("(no bonus", breakdowns[1])
        self.assertEqual(by_id[2], 8)  # 6 + capped RSS bonus 2
        self.assertEqual(by_id[3], 10)  # 9 + 2 capped at 10
        self.assertEqual(by_id[4], 0)
        self.assertEqual(breakdowns[4], "0 (irrelevant)")
        self.assertEqual(stats["irrelevant"], 1)
        # Irrelevant docs persist no summary.
        synced = [c.args[0] for c in
                  mock_db.update_event_summary_by_link.call_args_list]
        self.assertNotIn(4, synced)

    def test_recency_boundary(self):
        now = time.time()
        docs = [_make_doc(1, data={"published_at": now - 23 * 3600}),
                _make_doc(2, data={"published_at": now - 25 * 3600}),
                _make_doc(3, data={"published_at": "not-a-time"})]
        evals = [_make_eval(i, quality_score=6) for i in (1, 2, 3)]
        _, mock_db, _ = _run_intelligence(docs, [_batch_result(evals)])
        by_id = {c.args[0]: c.args[1]
                 for c in mock_db.update_intelligence.call_args_list}
        # Source boost is 0 here (source=""); only recency differentiates.
        self.assertEqual(by_id[1], 7)  # recent (<24h): +1
        self.assertEqual(by_id[2], 6)  # stale: no bonus
        self.assertEqual(by_id[3], 6)  # malformed timestamp: no throw, no bonus

    def test_trend_gate_and_first_party(self):
        docs = [_make_doc(1, title="Critical zero-day CVE-2026 patch released"),
                _make_doc(2, title="Critical zero-day CVE-2026 patch released"),
                _make_doc(3, url="https://openai.com/blog/new-model")]
        # The trend gate reads the MODEL's category, not the doc's.
        evals = [_make_eval(1, quality_score=6),
                 _make_eval(2, quality_score=6, category="Hackathons"),
                 _make_eval(3, quality_score=6)]
        _, mock_db, _ = _run_intelligence(docs, [_batch_result(evals)])
        breakdowns = {}
        scores = {}
        for c in mock_db.update_intelligence.call_args_list:
            breakdowns[c.args[0]] = c.kwargs["score_breakdown"]
            scores[c.args[0]] = c.args[1]
        self.assertIn("trend=1", breakdowns[1])  # News hot terms
        self.assertIn("trend=0", breakdowns[2])  # category gate
        self.assertIn("trend=0", breakdowns[3])  # plain title, first-party only
        self.assertEqual(scores[3], 7)  # 6 + first-party bonus only

    def test_category_normalization(self):
        docs = [_make_doc(i) for i in (1, 2, 3, 4)]
        evals = [_make_eval(1, category="news "),
                 _make_eval(2, category="HACKATHONS"),
                 _make_eval(3, category="tutorial"),
                 _make_eval(4, category="")]
        _, mock_db, _ = _run_intelligence(docs, [_batch_result(evals)])
        cats = {c.args[0]: c.kwargs["category"]
                for c in mock_db.update_intelligence.call_args_list}
        self.assertEqual(cats, {1: "News", 2: "Hackathons", 3: "News", 4: "News"})


class TestValidationAndLimits(unittest.TestCase):
    def test_validate_batch_output_matrix(self):
        from src.agents.intelligence import validate_batch_output

        good = _make_eval("1")
        dup = _make_eval("1", quality_score=5)
        unknown = _make_eval("999")
        blank = _make_eval("2")
        blank.item_id = ""
        out = SimpleNamespace(output=SimpleNamespace(
            evaluations=[good, dup, unknown, blank, "garbage"]))
        from src.agents.intelligence import IntelligenceBatchResult  # noqa
        mapped, problems = validate_batch_output(out.output, ["1", "2", "3"])
        self.assertEqual(set(mapped), {"1"})  # first-seen kept
        self.assertEqual(sorted(problems["missing"]), ["2", "3"])
        self.assertEqual(problems["duplicate"], ["1"])
        self.assertEqual(problems["unknown"], ["999"])
        self.assertEqual(problems["malformed"], 2)  # blank id + garbage

    def test_missing_retry_recovery(self):
        docs = [_make_doc(1), _make_doc(2), _make_doc(3)]
        first = _batch_result([_make_eval(1), _make_eval(3)])
        second = _batch_result([_make_eval(2)])
        stats, mock_db, _ = _run_intelligence(docs, [first, second])
        updated = {c.args[0] for c in mock_db.update_intelligence.call_args_list}
        self.assertEqual(updated, {1, 2, 3})
        self.assertEqual(stats.get("failed", 0), 0)

    def test_no_split_on_429(self):
        docs = [_make_doc(1), _make_doc(2), _make_doc(3)]
        err = Exception("LLM API returned 429. Please try again in 1s")
        stats, mock_db, mock_run = _run_intelligence(docs, err)
        # run_agent_with_retry owns 429 retries (1 + 5); every attempt carries
        # the full 3-doc group — never split into smaller batches.
        self.assertEqual(mock_run.call_count, 6)
        for c in mock_run.call_args_list:
            self.assertEqual(c.args[0].count("[ITEM "), 3)
        self.assertEqual(stats.get("failed"), 3)
        mock_db.update_intelligence.assert_not_called()

    def test_no_split_on_auth_error(self):
        docs = [_make_doc(1), _make_doc(2)]
        stats, _, mock_run = _run_intelligence(
            docs, Exception("invalid api key"))
        self.assertEqual(mock_run.call_count, 1)
        self.assertEqual(stats.get("failed"), 2)
        self.assertGreaterEqual(stats["llm_errors"], 1)

    def test_split_depth_budget_bounds_attempts(self):
        # Persistently failing 8-group: 1 + 2 + 4 + 8 = 15 attempts max,
        # then stop with all docs failed (never silently persisted).
        docs = [_make_doc(i) for i in range(1, 9)]
        stats, mock_db, mock_run = _run_intelligence(docs, Exception("boom"))
        self.assertLessEqual(mock_run.call_count, 15)
        self.assertEqual(stats.get("failed"), 8)
        mock_db.update_intelligence.assert_not_called()

    def test_empty_queue_stats_shape(self):
        stats, _, mock_run = _run_intelligence([], [])
        mock_run.assert_not_called()
        self.assertEqual(stats, {
            "total_evaluated": 0, "processed": 0, "irrelevant": 0,
            "skipped_invalid_ig": 0, "by_ig": {}, "by_category": {},
            "by_score": {"9-10": 0, "7-8": 0, "5-6": 0, "1-4": 0, "0": 0},
            "llm_errors": 0, "llm_retries": 0, "batches": 0, "failed": 0,
            "splits": 0, "hard_fails": 0, "quota_used_high_watermark": 0,
            "ig_assignments": 0, "membership_histogram": {}, "fallback_used": 0,
        })

    def test_default_pins(self):
        import inspect
        from src.agents import intelligence as intel
        from src.config import settings

        self.assertEqual(intel.INTELLIGENCE_BATCH_SIZE, 8)
        params = inspect.signature(intel.run_intelligence).parameters
        self.assertIs(params["batch_size"].default, intel.INTELLIGENCE_BATCH_SIZE)
        self.assertEqual(settings.MAX_IGS_PER_RUN, 30)
        self.assertEqual(settings.INTELLIGENCE_MAX_DOCS_PER_RUN, 30)
        self.assertEqual(settings.NOTIFY_PER_IG_LIMIT, 20)
        self.assertEqual(settings.DISCOVERY_MAX_QUERIES_PER_IG, 2)
        self.assertEqual(settings.SEARCH_MAX_RESULTS_PER_QUERY, 3)
        self.assertIs(settings.SEARCH_ENABLED, False)


if __name__ == "__main__":
    unittest.main()
