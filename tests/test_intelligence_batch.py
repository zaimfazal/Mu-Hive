"""tests/test_intelligence_batch.py
==================================
Batched Intelligence classification (Phase 2B).

Offline: the LLM agent and DB facade are mocked; no provider is contacted.
Follows tests/test_orchestrator_writer.py conventions (unittest, dummy key,
AsyncMock agent, mocked Database facade).
"""

import asyncio
import os
import time
from collections import Counter
from types import SimpleNamespace

# Must be set before importing src.agents.intelligence (agent_config exits
# without a key). Dummy value only; the LLM is always mocked below.
os.environ.setdefault("GROQ_API_KEY", "test-dummy-key")

import unittest
from unittest.mock import AsyncMock, patch


def _run(coro):
    return asyncio.run(coro)


def _make_doc(i, **kw):
    doc = {
        "_id": i,
        "title": f"Test article {i}",
        "summary": f"Content about AI breakthrough {i}",
        "scraped_full_text": None,
        "category": "Unknown",
        "ig_tags": ["AI"],
        "url": f"https://example.com/{i}",
        "link": f"https://example.com/{i}",
        "source": "RSS",
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


def _result_for_prompt(prompt):
    """Build a valid batch result matching every [ITEM id] in the prompt."""
    import re

    ids = re.findall(r"\[ITEM (\S+)\]", prompt)
    return _batch_result([_make_eval(i) for i in ids])


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


def _updated_ids(mock_db):
    return [c.args[0] for c in mock_db.update_intelligence.call_args_list]


class TestBatchedIntelligence(unittest.TestCase):
    def test_batch_of_8_single_request(self):
        docs = [_make_doc(i) for i in range(1, 9)]
        stats, mock_db, mock_run = _run_intelligence(
            docs, [_batch_result([_make_eval(i) for i in range(1, 9)])]
        )
        self.assertEqual(mock_run.call_count, 1)
        self.assertEqual(mock_db.update_intelligence.call_count, 8)
        self.assertEqual(stats["total_evaluated"], 8)
        self.assertEqual(stats["processed"], 8)

    def test_small_batch(self):
        docs = [_make_doc(i) for i in range(1, 4)]
        stats, mock_db, mock_run = _run_intelligence(
            docs, [_batch_result([_make_eval(i) for i in range(1, 4)])]
        )
        self.assertEqual(mock_run.call_count, 1)
        self.assertEqual(mock_db.update_intelligence.call_count, 3)
        self.assertEqual(stats["total_evaluated"], 3)

    def test_out_of_order_id_mapping(self):
        docs = [_make_doc(1), _make_doc(2), _make_doc(3)]
        evals = [
            _make_eval(3, quality_score=9, ig_tags=["AI"]),
            _make_eval(1, quality_score=6, ig_tags=["Web Development"]),
            _make_eval(2, quality_score=7, ig_tags=["Cyber Security"]),
        ]
        _, mock_db, _ = _run_intelligence(docs, [_batch_result(evals)])
        by_id = {}
        for c in mock_db.update_intelligence.call_args_list:
            by_id[c.args[0]] = (c.args[1], c.args[2])
        # Order-independent: each doc got its own evaluation's score/tags.
        self.assertEqual(by_id[3][1], ["AI"])
        self.assertEqual(by_id[1][1], ["Web Development"])
        self.assertEqual(by_id[2][1], ["Cyber Security"])
        self.assertNotEqual(by_id[1][0], by_id[3][0])

    def test_missing_ids_not_marked_irrelevant(self):
        docs = [_make_doc(1), _make_doc(2), _make_doc(3)]
        first = _batch_result([_make_eval(1), _make_eval(3)])
        # Retry of the missing doc also omits it: it stays unprocessed.
        second = _batch_result([_make_eval(1), _make_eval(3)])
        stats, mock_db, _ = _run_intelligence(docs, [first, second])
        ids = _updated_ids(mock_db)
        self.assertIn(1, ids)
        self.assertIn(3, ids)
        self.assertNotIn(2, ids)  # never silently classified irrelevant
        self.assertEqual(stats.get("failed"), 1)

    def test_duplicate_ids_persisted_once(self):
        docs = [_make_doc(1), _make_doc(2)]
        dup = _batch_result([_make_eval(1), _make_eval(1), _make_eval(2)])
        _, mock_db, _ = _run_intelligence(docs, [dup])
        counts = Counter(_updated_ids(mock_db))
        self.assertEqual(counts[1], 1)
        self.assertEqual(counts[2], 1)

    def test_unknown_ids_rejected(self):
        docs = [_make_doc(1), _make_doc(2)]
        res = _batch_result([_make_eval(1), _make_eval(2), _make_eval(999)])
        stats, mock_db, _ = _run_intelligence(docs, [res])
        self.assertNotIn(999, _updated_ids(mock_db))
        self.assertEqual(stats["total_evaluated"], 2)

    def test_malformed_output_leaves_docs_unprocessed(self):
        docs = [_make_doc(1), _make_doc(2)]
        bad = SimpleNamespace(output="not-a-batch-result")
        # Initial batch + one single-doc retry per doc, all malformed.
        stats, mock_db, mock_run = _run_intelligence(docs, [bad, bad, bad])
        mock_db.update_intelligence.assert_not_called()
        self.assertGreaterEqual(stats["llm_errors"], 1)
        self.assertEqual(stats.get("failed"), 2)
        self.assertLessEqual(mock_run.call_count, 1 + 2)

    def test_transient_failure_bounded_retry(self):
        docs = [_make_doc(1), _make_doc(2)]
        good = _batch_result([_make_eval(1), _make_eval(2)])
        err = Exception("LLM API returned 429. Please try again in 1s")
        stats, mock_db, mock_run = _run_intelligence(docs, [err, good])
        self.assertEqual(mock_run.call_count, 2)
        self.assertEqual(mock_db.update_intelligence.call_count, 2)
        self.assertEqual(stats["total_evaluated"], 2)

    def test_batch_splits_after_repeated_failure(self):
        docs = [_make_doc(i) for i in range(1, 9)]

        def _flaky(prompt):
            import re

            ids = re.findall(r"\[ITEM (\S+)\]", prompt)
            if len(ids) > 4:
                raise Exception("boom")
            return _batch_result([_make_eval(i) for i in ids])

        stats, mock_db, mock_run = _run_intelligence(docs, _flaky)
        self.assertEqual(mock_db.update_intelligence.call_count, 8)
        self.assertEqual(stats["total_evaluated"], 8)
        # 1 failed batch of 8 + 2 successful batches of 4.
        self.assertEqual(mock_run.call_count, 3)

    def test_tag_validation_and_scoring_preserved(self):
        doc = _make_doc(
            1,
            url="https://openai.com/blog/new-model",
            data={"published_at": time.time()},
        )
        ev = _make_eval(1, quality_score=7, ig_tags=["ai"])
        _, mock_db, _ = _run_intelligence([doc], [_batch_result([ev])])
        c = mock_db.update_intelligence.call_args
        # RSS(+2) + recency(+1) + first-party(+1), capped at +2 -> 7+2=9.
        self.assertEqual(c.args[0], 1)
        self.assertEqual(c.args[1], 9)
        self.assertEqual(c.args[2], ["AI"])
        self.assertIn("7 + 2 bonus", c.kwargs["score_breakdown"])
        mock_db.update_event_summary_by_link.assert_called_once()

    def test_no_silent_loss(self):
        docs = [_make_doc(i) for i in range(1, 6)]
        first = _batch_result([_make_eval(1), _make_eval(2), _make_eval(3)])
        second = _batch_result([_make_eval(4), _make_eval(5)])
        stats, mock_db, _ = _run_intelligence(docs, [first, second])
        self.assertEqual(set(_updated_ids(mock_db)), {1, 2, 3, 4, 5})
        self.assertEqual(stats["total_evaluated"], 5)
        self.assertEqual(stats.get("failed", 0), 0)

    def test_no_duplicate_persistence(self):
        docs = [_make_doc(i) for i in range(1, 7)]

        def _ok(prompt):
            return _result_for_prompt(prompt)

        stats, mock_db, mock_run = _run_intelligence(docs, _ok, batch_size=3)
        self.assertEqual(mock_run.call_count, 2)
        counts = Counter(_updated_ids(mock_db))
        self.assertTrue(all(v == 1 for v in counts.values()))
        self.assertEqual(len(counts), 6)
        link_counts = Counter(
            c.args[0] for c in mock_db.update_event_summary_by_link.call_args_list
        )
        self.assertTrue(all(v <= 1 for v in link_counts.values()))

    def test_provider_fallback_compatibility(self):
        from src.agents import intelligence as intel

        # Batch agent reuses the existing provider fallback chain/model.
        self.assertIs(
            intel.batch_intelligence_agent.model, intel.intelligence_agent.model
        )
        docs = [_make_doc(1)]
        good = _batch_result([_make_eval(1)])
        err = Exception("LLM API returned 429. Please try again in 1s")
        stats, mock_db, _ = _run_intelligence(docs, [err, good])
        self.assertEqual(stats["total_evaluated"], 1)
        self.assertEqual(mock_db.update_intelligence.call_count, 1)

    def test_100_docs_about_13_initial_requests(self):
        docs = [_make_doc(i) for i in range(1, 101)]

        def _ok(prompt):
            return _result_for_prompt(prompt)

        stats, _, mock_run = _run_intelligence(docs, _ok)
        self.assertEqual(mock_run.call_count, 13)
        self.assertEqual(stats["total_evaluated"], 100)
        sizes = [c.args[0].count("[ITEM ") for c in mock_run.call_args_list]
        self.assertEqual(sizes, [8] * 12 + [4])


if __name__ == "__main__":
    unittest.main()
