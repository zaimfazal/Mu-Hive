"""tests/test_registry_rollout.py
================================
Phase 4B registry-driven discovery and distribution (mocked, offline).

Covers: registry sizes (5/20/30), inactive groups, aliases, missing and
shared search queries, shared RSS feeds, missing destinations, override
precedence, secondary-IG planner retrieval, per-IG/total limits, URL
deduplication with multiple IG associations, and notification-format
preservation (via the Phase 4A suite in the full run). No real search,
SMTP, Zulip, or LLM calls.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.modules.setdefault("zulip", MagicMock())

from src.config.interest_groups import (
    InterestGroupRegistry,
    apply_ig_cap,
    shared_query_index,
)


def _write_registry(tmpdir, specs):
    """specs: list of dicts {name, active=True, queries=True, channel, email}."""
    lines = ["interest_groups:"]
    for i, spec in enumerate(specs):
        name = spec["name"]
        lines.append(f'  - id: "ig_{i}"')
        lines.append(f'    name: "{name}"')
        lines.append('    description: "Test group."')
        lines.append("    keywords: []")
        lines.append(f'    aliases: ["{name.lower()}"]')
        if spec.get("queries", True):
            lines.append("    search_queries:")
            lines.append(f'      news: ["{name} breakthroughs 2026"]')
            lines.append(f'      hackathons: ["{name} hackathon 2026"]')
        else:
            lines.append("    search_queries: {}")
        lines.append("    negative_indicators: []")
        lines.append(f"    is_active: {'true' if spec.get('active', True) else 'false'}")
        channel = spec.get("channel")
        lines.append(f'    zulip_channel: "{channel}"' if channel else "    zulip_channel: null")
        email = spec.get("email")
        lines.append(f'    email: "{email}"' if email else "    email: null")
    path = os.path.join(tmpdir, "igs.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def _specs(n, **overrides):
    return [{"name": f"IG {i}", **overrides} for i in range(n)]


def _canned_row(ig="AI"):
    return {
        "title": f"{ig} item", "summary": "S", "link": "https://x/1",
        "quality_score": 8, "source_engine": "RSS", "created_at": None,
        "structured_metadata": {},
    }


class TestRegistryEnumeration(unittest.TestCase):
    def _planned_igs(self, registry, n_igs=3):
        from src.agents import planner as planner_module

        mock_db = MagicMock()
        mock_db.get_top_opportunities_by_ig_and_category.return_value = [
            _canned_row()
        ]
        with patch.object(planner_module, "Database", return_value=mock_db), \
             patch.object(planner_module, "registry", registry):
            digests = planner_module.plan_digests()
        calls = mock_db.get_top_opportunities_by_ig_and_category.call_args_list
        queried = sorted({c.args[0] for c in calls})
        limits = {c.kwargs.get("limit", c.args[2] if len(c.args) > 2 else None)
                  for c in calls}
        return digests, queried, limits

    def test_planner_enumerates_5_20_30_active_igs(self):
        for n in (5, 20, 30):
            with self.subTest(n=n):
                with tempfile.TemporaryDirectory() as tmp:
                    path = _write_registry(tmp, _specs(n))
                    reg = InterestGroupRegistry(config_path=path)
                    self.assertEqual(len(reg.all_active_names()), n)
                    digests, queried, limits = self._planned_igs(reg)
                    self.assertEqual(queried, sorted(reg.all_active_names()))
                    self.assertEqual(limits, {5})  # per-IG preview limit intact
                    self.assertEqual(sorted(digests.keys()),
                                     sorted(reg.all_active_names()))

    def test_inactive_groups_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_registry(tmp, _specs(3) + [
                {"name": "Old IG", "active": False},
                {"name": "Dead IG", "active": False},
            ])
            reg = InterestGroupRegistry(config_path=path)
            digests, queried, _ = self._planned_igs(reg)
            self.assertEqual(queried, ["IG 0", "IG 1", "IG 2"])
            self.assertNotIn("Old IG", digests)

    def test_zero_active_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_registry(tmp, [{"name": "Off IG", "active": False}])
            reg = InterestGroupRegistry(config_path=path)
            from src.agents import planner as planner_module

            with patch.object(planner_module, "registry", reg):
                self.assertEqual(planner_module.plan_digests(), {})

    def test_aliases_and_canonical_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_registry(tmp, [{"name": "AI"}])
            reg = InterestGroupRegistry(config_path=path)
            self.assertEqual(reg.normalize("ai"), "AI")
            self.assertEqual(reg.normalize("  AI  "), "AI")
            self.assertIsNone(reg.normalize("nope"))
            self.assertIsNone(reg.normalize(""))


class TestSearchPlan(unittest.TestCase):
    def test_missing_queries_not_discovery_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_registry(tmp, [
                {"name": "Ready IG"},
                {"name": "Queryless IG", "queries": False},
            ])
            reg = InterestGroupRegistry(config_path=path)
            plan = reg.search_plan()
            self.assertIn("Ready IG", plan)
            self.assertNotIn("Queryless IG", plan)
            readiness = reg.discovery_readiness(feeds_by_ig={"Ready IG": ["u"]})
            self.assertTrue(readiness["Ready IG"]["queries"])
            self.assertFalse(readiness["Queryless IG"]["queries"])
            self.assertFalse(readiness["Queryless IG"]["feeds"])

    def test_per_ig_query_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_registry(tmp, [{"name": "AI"}])
            reg = InterestGroupRegistry(config_path=path)
            plan = reg.search_plan(max_queries_per_ig=1)
            self.assertEqual(len(plan["AI"]["news"]), 1)
            self.assertEqual(len(plan["AI"]["hackathons"]), 1)
            self.assertEqual(reg.search_plan(max_queries_per_ig=0).get("AI", {}), {})

    def test_shared_queries_deduplicated(self):
        plan = {
            "AI": {"news": ["shared q", "ai only"], "hackathons": []},
            "GenAI": {"news": ["shared q"], "hackathons": ["gen hack"]},
        }
        index = shared_query_index(plan)
        self.assertEqual(sorted(index.keys()), ["ai only", "gen hack", "shared q"])
        self.assertEqual(sorted(index["shared q"]),
                         [("AI", "news"), ("GenAI", "news")])

    def test_group_feeds_by_url(self):
        from src.config.sources import group_feeds_by_url

        grouped = group_feeds_by_url({
            "AI": ["https://a/feed", "https://shared/feed"],
            "Web Development": ["https://shared/feed", "https://b/feed"],
        })
        self.assertEqual(grouped["https://shared/feed"], ["AI", "Web Development"])
        self.assertEqual(grouped["https://a/feed"], ["AI"])
        self.assertEqual(len(grouped), 3)


class TestRoutingPrecedence(unittest.TestCase):
    def test_zulip_channel_map_overrides_registry(self):
        import scripts.zulip_notify as zn

        fake_registry = MagicMock()
        fake_registry.get_zulip_channels.return_value = {
            "AI": "Other Channel", "Generative AI": "AI IG",
        }
        with patch("src.config.interest_groups.registry", fake_registry):
            channels = zn.resolve_zulip_channels()
        # Legacy explicit map wins for the original five.
        self.assertEqual(channels["AI"], "AI IG")
        # Registry-only IGs flow through.
        self.assertEqual(channels["Generative AI"], "AI IG")
        # Registry IGs without channels are absent (skipped, unmarked).
        self.assertNotIn("Space", channels)

    def test_email_db_overrides_registry(self):
        import scripts.gmailsender as gm

        resolved = gm.resolve_email_recipients([
            {"ig": "ai", "email": "override@example.com"},
            {"ig": "Generative AI", "email": "gen@example.com"},
        ])
        # DB row wins over the registry address (real registry has ai-digest@).
        self.assertEqual(resolved["AI"], "override@example.com")
        # Registry-only addresses still flow (real registry emails).
        self.assertIn("Web Development", resolved)
        # DB-only IGs are added.
        self.assertEqual(resolved["Generative AI"], "gen@example.com")
        # Empty input falls back to the registry base, not an error.
        self.assertEqual(gm.resolve_email_recipients([])["AI"],
                         "ai-digest@mulearn.org")

    def test_apply_ig_cap(self):
        kept, skipped = apply_ig_cap(["a", "b", "c"], 2)
        self.assertEqual((kept, skipped), (["a", "b"], ["c"]))
        self.assertEqual(apply_ig_cap(["a"], None), (["a"], []))
        self.assertEqual(apply_ig_cap(["a"], "bogus"), (["a"], []))


class TestSearchRunner(unittest.TestCase):
    def test_shared_query_single_call_multi_ig_inserts(self):
        from src.scraping import search_engine as se

        mock_db = MagicMock()
        mock_db.link_exists.return_value = False
        mock_db.insert_event.return_value = 1
        mock_ddgs = MagicMock()
        mock_ddgs.return_value.text.return_value = [
            {"title": "T", "href": "https://x/shared", "body": "B"},
        ]
        index = {"shared q": [("AI", "news"), ("Web Development", "news")]}
        with patch("ddgs.DDGS", mock_ddgs):
            stats = se.run_registry_search(mock_db, index, max_results=3)
        # One provider call for the shared query...
        self.assertEqual(mock_ddgs.return_value.text.call_count, 1)
        # ...attributed to both IGs with per-(link, IG) dedup semantics.
        inserted = sorted(c.args[1:3]
                          for c in mock_db.insert_event.call_args_list)
        self.assertEqual(inserted,
                         [("https://x/shared", "AI"),
                          ("https://x/shared", "Web Development")])
        self.assertEqual(stats["queries_run"], 1)
        self.assertEqual(stats["items_inserted"], 2)
        self.assertEqual(stats["igs_covered"], 2)

    def test_unresolvable_ig_and_empty_plan(self):
        from src.scraping import search_engine as se

        mock_db = MagicMock()
        stats = se.run_registry_search(mock_db, {}, max_results=3)
        self.assertEqual(stats["queries_run"], 0)
        mock_db.insert_event.assert_not_called()

    def test_duplicate_results_deduplicated_per_ig(self):
        from src.scraping import search_engine as se

        seen = set()
        mock_db = MagicMock()
        mock_db.link_exists.side_effect = lambda link, ig: (link, ig) in seen
        mock_db.insert_event.side_effect = lambda *a: seen.add((a[1], a[2])) or 1
        mock_ddgs = MagicMock()
        mock_ddgs.return_value.text.return_value = [
            {"title": "T", "href": "https://x/1", "body": "B"},
            {"title": "T", "href": "https://x/1", "body": "B"},
        ]
        index = {"q": [("AI", "news")]}
        with patch("ddgs.DDGS", mock_ddgs):
            stats = se.run_registry_search(mock_db, index, max_results=3)
        self.assertEqual(stats["items_inserted"], 1)
        self.assertEqual(stats["duplicates_skipped"], 1)


class TestNotifierLimits(unittest.TestCase):
    def test_zulip_respects_total_cap(self):
        import asyncio
        import scripts.zulip_notify as zn
        from tests.test_delivery import (
            FakeConn, FakeState, _fake_zulip_get_top, _zulip_env, add_event,
        )

        state = FakeState()
        add_event(state, "E", "AI", "News", 9)
        writer = MagicMock()
        writer.client.send_message = MagicMock(return_value={"id": 1})
        # NOTE: notifiers bind settings at call time via local import, so a
        # module-attr patch takes effect for the duration of the run.
        with patch("scripts.zulip_notify.psycopg2") as mock_pg, \
             patch("scripts.zulip_notify.ZulipWriter", return_value=writer), \
             patch.object(zn.db, "get_top_opportunities_by_ig_and_category",
                          create=True,
                          side_effect=_fake_zulip_get_top(state)), \
             patch("src.config.settings.MAX_IGS_PER_RUN", 1), \
             patch.dict(os.environ, _zulip_env(), clear=True):
            mock_pg.connect.return_value = FakeConn(state)
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            stats = loop.run_until_complete(zn.run_zulip_notifications())
        total = len(zn.resolve_zulip_channels())
        self.assertEqual(stats["igs_skipped_cap"], total - 1)
        self.assertEqual(stats["igs_processed"], 1)

    def test_gmail_limit_param(self):
        import scripts.gmailsender as gm
        from tests.test_delivery import FakeConn, FakeState, add_event

        state = FakeState()
        add_event(state, "E", "AI", "News", 9)
        state.ig_mails = [{"ig": "AI", "email": "ai@example.com"}]
        with patch("smtplib.SMTP"), \
             patch("scripts.gmailsender.psycopg2") as mock_pg, \
             patch("scripts.gmailsender.send_email",
                   return_value=None) as mock_send, \
             patch("src.config.settings.NOTIFY_PER_IG_LIMIT", 2), \
             patch.dict(os.environ,
                        {"GMAIL_SENDER": "s@x", "GMAIL_APP_PASSWORD": "p"},
                        clear=True):
            mock_pg.connect.return_value = FakeConn(state)
            gm.run_email_agent()
        self.assertEqual(mock_send.call_count, 1)
        limits = [p[-1] for sql, p in state.statements
                  if "FROM events e" in sql]
        self.assertTrue(limits)
        self.assertTrue(all(v == 2 for v in limits))


if __name__ == "__main__":
    unittest.main()
