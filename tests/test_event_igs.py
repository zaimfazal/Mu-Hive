"""tests/test_event_igs.py
==========================
Multi-IG junction-table persistence (Phase 3).

Offline: db_conn.get_cursor is replaced with an in-memory FakeCursor, so no
PostgreSQL server is needed. There is no configured test database in this
repo (no docker/testcontainers/DATABASE_URL test harness), so round trips
run against the fake; the retrieval SQL string is additionally asserted to
contain the junction JOIN.
"""

import unittest
from unittest.mock import patch

from src.db.connection import db_conn


class FakeState:
    def __init__(self):
        self.events = {}  # id -> dict row
        self.by_link = {}  # apply_link -> id
        self.next_id = 1
        self.igs = set()  # (event_id, ig_name)
        self.scraped_row = ("T", "https://x/1", "RSS", {})
        self.statements = []


class FakeCursor:
    """Minimal in-memory emulation of the SQL used by the junction paths."""

    def __init__(self, state):
        self.state = state
        self._fetch = None
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        params = params or ()
        self.state.statements.append((sql, tuple(params)))
        s = " ".join(sql.split())
        st = self.state
        if s.startswith("INSERT INTO event_interest_groups"):
            st.igs.add((params[0], params[1]))
            self.rowcount = 1
        elif s.startswith("DELETE FROM event_interest_groups"):
            if len(params) == 2:  # ... AND ig_name NOT IN %s
                keep = set(params[1])
                st.igs = {(e, g) for (e, g) in st.igs
                          if not (e == params[0] and g not in keep)}
            else:
                st.igs = {(e, g) for (e, g) in st.igs if e != params[0]}
            self.rowcount = 1
        elif s.startswith("INSERT INTO events"):
            (title, ig, category, summary, link, score, platform,
             location, days_left, deadline, _now) = params
            if link in st.by_link:
                eid = st.by_link[link]
                st.events[eid].update({
                    "title": title, "ig": ig, "category": category,
                    "summary": summary, "validity_score": score,
                    "platform": platform, "location": location,
                    "days_left": days_left, "deadline": deadline,
                })
            else:
                eid = st.next_id
                st.next_id += 1
                st.events[eid] = {
                    "id": eid, "title": title, "ig": ig, "category": category,
                    "summary": summary, "apply_link": link,
                    "validity_score": score, "platform": platform,
                    "location": location, "days_left": days_left,
                    "deadline": deadline, "zulip_sent": False,
                    "mail_sent": False,
                }
                st.by_link[link] = eid
            self._fetch = (eid,)
            self.rowcount = 1
        elif s.startswith("SELECT id FROM events WHERE apply_link"):
            eid = st.by_link.get(params[0])
            self._fetch = (eid,) if eid is not None else None
            self.rowcount = 1 if eid is not None else 0
        elif s.startswith("SELECT title, url, source, data FROM scraped_data"):
            self._fetch = st.scraped_row
            self.rowcount = 1
        elif s.startswith("UPDATE scraped_data"):
            self.rowcount = 1
        elif s.startswith("UPDATE events"):
            # orchestrator_writer branch: params end with the event id
            eid = params[-1]
            if eid in st.events:
                (title, ig, category, summary, score, platform,
                 location, days_left, deadline, _now, _eid) = params
                st.events[eid].update({
                    "title": title, "ig": ig, "category": category,
                    "summary": summary, "validity_score": score,
                    "platform": platform, "location": location,
                    "days_left": days_left, "deadline": deadline,
                })
            self.rowcount = 1
        else:
            # DDL / schema guards / anything else: tolerated, not emulated.
            self.rowcount = 0

    def fetchone(self):
        out = self._fetch
        self._fetch = None
        return out

    def fetchall(self):
        return []


def _patched_state(state):
    return patch.object(
        db_conn, "get_cursor",
        side_effect=lambda factory=None: FakeCursor(state),
    )


def query_top_fake(state, ig, category, limit=5):
    """Mirror of the new retrieval semantics: legacy OR junction, deduped."""
    seen = {}
    for eid, row in state.events.items():
        if row["category"] != category:
            continue
        if not (row["ig"] == ig or (eid, ig) in state.igs):
            continue
        link = row["apply_link"]
        if link not in seen or row["validity_score"] > seen[link]["validity_score"]:
            seen[link] = dict(row)
    ordered = sorted(seen.values(),
                     key=lambda r: (-(r["validity_score"] or 0), r["id"]))
    return ordered[:limit]


class TestEventInterestGroups(unittest.TestCase):
    def test_single_ig_round_trip(self):
        from src.db.repositories.event_repository import EventRepository

        state = FakeState()
        with _patched_state(state):
            eid = EventRepository.upsert({
                "title": "T", "ig": "AI", "category": "News",
                "summary": "S", "apply_link": "https://x/1",
                "validity_score": 8,
            })
        self.assertEqual(len(state.events), 1)
        self.assertEqual(state.igs, {(eid, "AI")})
        self.assertEqual(state.events[eid]["ig"], "AI")

    def test_multi_ig_round_trip(self):
        from src.db.repositories.event_repository import EventRepository

        state = FakeState()
        with _patched_state(state):
            eid = EventRepository.upsert({
                "title": "T", "ig": "AI",
                "ig_names": ["AI", "Web Development"],
                "category": "News", "summary": "S",
                "apply_link": "https://x/1", "validity_score": 8,
            })
        self.assertEqual(len(state.events), 1)  # still one canonical event
        self.assertEqual(state.igs, {(eid, "AI"), (eid, "Web Development")})
        self.assertEqual(state.events[eid]["ig"], "AI")  # legacy primary = tags[0]

    def test_membership_change_removes_stale(self):
        from src.db.repositories.event_repository import EventRepository

        state = FakeState()
        base = {"title": "T", "ig": "AI", "category": "News",
                "summary": "S", "apply_link": "https://x/1",
                "validity_score": 8}
        with _patched_state(state):
            eid = EventRepository.upsert({**base, "ig_names": ["AI", "Web Development"]})
            EventRepository.upsert({**base, "ig_names": ["AI"]})
        self.assertEqual(len(state.events), 1)
        self.assertEqual(state.igs, {(eid, "AI")})

    def test_repeated_ingestion_idempotent(self):
        from src.db.repositories.event_repository import EventRepository

        state = FakeState()
        payload = {"title": "T", "ig": "AI",
                   "ig_names": ["AI", "Web Development", "AI", "Unknown", " "],
                   "category": "News", "summary": "S",
                   "apply_link": "https://x/1", "validity_score": 8}
        with _patched_state(state):
            for _ in range(3):
                EventRepository.upsert(dict(payload))
        self.assertEqual(len(state.events), 1)
        self.assertEqual(state.igs,
                         {(state.by_link["https://x/1"], "AI"),
                          (state.by_link["https://x/1"], "Web Development")})

    def test_sync_statements_replace_not_append(self):
        from src.db.repositories.event_repository import EventRepository

        state = FakeState()
        with _patched_state(state):
            with db_conn.get_cursor() as cur:
                EventRepository.sync_interest_groups(cur, 7, ["AI", "AI", None, " "])
        kinds = [sql.split()[0] for sql, _ in state.statements]
        self.assertIn("DELETE", kinds)
        inserts = [p for sql, p in state.statements
                   if sql.strip().startswith("INSERT INTO event_interest_groups")]
        self.assertEqual(inserts, [(7, "AI")])
        # Empty list clears memberships without inserting.
        state.statements.clear()
        with _patched_state(state):
            with db_conn.get_cursor() as cur:
                EventRepository.sync_interest_groups(cur, 7, [])
        self.assertEqual(state.igs, set())
        self.assertTrue(all(sql.strip().startswith("DELETE")
                            for sql, _ in state.statements))

    def test_update_intelligence_syncs_full_tags(self):
        from src.db.postgres_database import DatabaseFacade

        state = FakeState()
        with _patched_state(state):
            db = DatabaseFacade()
            db.update_intelligence(11, 9, ["AI", "Web Development"],
                                   generated_summary="Sum",
                                   category="News")
        # (update_intelligence's boolean return reflects the last cursor
        # rowcount, not success; assert on persisted state instead.)
        eid = state.by_link["https://x/1"]
        self.assertEqual(state.igs, {(eid, "AI"), (eid, "Web Development")})
        self.assertEqual(state.events[eid]["ig"], "AI")

    def test_reclassification_refreshes_memberships(self):
        from src.db.postgres_database import DatabaseFacade

        state = FakeState()
        with _patched_state(state):
            db = DatabaseFacade()
            db.update_intelligence(11, 9, ["AI", "Web Development"], category="News")
            self.assertEqual(len(state.events), 1)
            db.update_intelligence(11, 8, ["AI"], category="News")
        eid = state.by_link["https://x/1"]
        self.assertEqual(len(state.events), 1)
        self.assertEqual(state.igs, {(eid, "AI")})

    def test_retrieval_through_secondary_ig(self):
        state = FakeState()
        state.events[1] = {"id": 1, "title": "T", "ig": "AI",
                           "category": "News", "apply_link": "https://x/1",
                           "validity_score": 8}
        state.igs = {(1, "AI"), (1, "Web Development")}
        got = query_top_fake(state, "Web Development", "News")
        self.assertEqual([r["id"] for r in got], [1])
        self.assertEqual(query_top_fake(state, "Cyber Security", "News"), [])

    def test_ranking_limits_no_duplicates(self):
        state = FakeState()
        for i in range(1, 8):
            state.events[i] = {"id": i, "title": f"T{i}",
                               "ig": "AI" if i % 2 else "Web Development",
                               "category": "News",
                               "apply_link": f"https://x/{i}",
                               "validity_score": i}
            state.igs.add((i, "AI"))  # every event also secondary in AI
        got = query_top_fake(state, "AI", "News", limit=5)
        self.assertEqual([r["id"] for r in got], [7, 6, 5, 4, 3])
        self.assertEqual(len({r["id"] for r in got}), len(got))

    def test_retrieval_sql_uses_junction(self):
        from src.db.postgres_database import DatabaseFacade

        state = FakeState()
        canned = [{
            "id": 1, "title": "T", "apply_link": "https://x/1", "ig": "AI",
            "category": "News", "summary": "S", "validity_score": 8,
            "platform": "RSS", "location": "Online", "deadline": None,
            "days_left": None, "zulip_sent": False, "created_at": None,
            "structured_metadata": {}, "source_engine": "RSS",
        }]

        class CannedCursor(FakeCursor):
            def fetchall(self):
                return [dict(r) for r in canned]

        with patch.object(db_conn, "get_cursor",
                          side_effect=lambda factory=None: CannedCursor(state)):
            db = DatabaseFacade.__new__(DatabaseFacade)
            db._orchestrator_since, db._orchestrator_until = None, None
            rows = DatabaseFacade.get_top_opportunities_by_ig_and_category(
                db, "Web Development", "News", limit=5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["link"], "https://x/1")
        text = " ".join(sql for sql, _ in state.statements
                        if "unique_events" in sql or "DISTINCT ON" in sql)
        self.assertIn("event_interest_groups", text)
        self.assertIn("jig.ig_name IS NOT NULL", text)
        params = [p for sql, p in state.statements if "DISTINCT ON" in sql][0]
        self.assertEqual(params[0], params[1])  # junction IG == legacy IG
        self.assertEqual(params[0], "Web Development")

    def test_delivery_flags_untouched_by_junction_writes(self):
        from src.db.repositories.event_repository import EventRepository

        state = FakeState()
        with _patched_state(state):
            EventRepository.upsert({
                "title": "T", "ig": "AI", "ig_names": ["AI", "Web Development"],
                "category": "News", "summary": "S",
                "apply_link": "https://x/1", "validity_score": 8,
            })
            with db_conn.get_cursor() as cur:
                EventRepository.sync_interest_groups(cur, 1, ["AI"])
        for sql, _ in state.statements:
            lowered = sql.lower()
            if "event_interest_groups" in lowered or "insert into events" in lowered:
                self.assertNotIn("mail_sent", lowered)
                self.assertNotIn("zulip_sent", lowered)

    def test_orchestrator_upsert_syncs_junction(self):
        from src.db import orchestrator_writer as writer

        state = FakeState()
        with _patched_state(state):
            eid = writer._upsert_event_without_constraint(
                title="T", ig="AI", summary="S",
                apply_link="https://x/9", validity_score=7,
            )
            again = writer._upsert_event_without_constraint(
                title="T2", ig="AI", summary="S2",
                apply_link="https://x/9", validity_score=8,
            )
        self.assertEqual(eid, again)
        self.assertEqual(len(state.events), 1)
        self.assertEqual(state.igs, {(eid, "AI")})


if __name__ == "__main__":
    unittest.main()
