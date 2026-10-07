"""tests/test_integration_postgres.py
=====================================
Phase 6 Supabase/PostgreSQL integration (opt-in) + offline checks.

Live tests run ONLY when MU_HIVE_TEST_DATABASE_URL points at an isolated
test database (never production). Without it they skip cleanly; the offline
schema-repeatability test always runs with a recording fake cursor.

Live tests touch only rows whose apply_link starts with 'itest-' and clean
them up afterwards (DELETE cascades to the junction/delivery tables). No
DROP/TRUNCATE/RLS/grant changes. Sends are never performed; delivery rows
are written directly to simulate post-send recording.
"""

import os
import unittest
import uuid
from contextlib import contextmanager
from unittest.mock import patch

TEST_URL = os.getenv("MU_HIVE_TEST_DATABASE_URL")
LIVE = bool(TEST_URL)
PREFIX = "itest-"


def _tag():
    return PREFIX + uuid.uuid4().hex[:8]


class RecordingCursor:
    def __init__(self):
        self.statements = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.statements.append((" ".join(sql.split()), params))

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class TestSchemaRepeatableOffline(unittest.TestCase):
    def test_initialize_schema_twice(self):
        from src.db.connection import db_conn
        from src.db import schema as schema_module

        for _ in range(2):
            cur = RecordingCursor()
            with patch.object(db_conn, "get_cursor", return_value=cur):
                schema_module.initialize_schema()
        text = "\n".join(sql for sql, _ in cur.statements)
        self.assertIn("event_interest_groups", text)
        self.assertIn("event_deliveries", text)
        self.assertIn("ON CONFLICT", text)


@contextmanager
def _test_db_cursors(test_conn):
    """Route the repo's shared db_conn cursors to the test connection."""
    from src.db.connection import db_conn

    class _Cur:
        def __init__(self, cur):
            self._cur = cur

        def __enter__(self):
            return self._cur

        def __exit__(self, *a):
            self._cur.close()
            return False

    with patch.object(
        db_conn, "get_cursor",
        side_effect=lambda factory=None: _Cur(
            test_conn.cursor(cursor_factory=factory)),
    ):
        yield


def _bare_facade():
    from src.db.postgres_database import DatabaseFacade

    with patch.object(DatabaseFacade, "__init__", lambda self: None):
        db = DatabaseFacade()
    db._orchestrator_since, db._orchestrator_until = None, None
    return db


@unittest.skipUnless(LIVE, "MU_HIVE_TEST_DATABASE_URL not set; live DB tests skipped")
class TestPostgresIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2
        cls.conn = psycopg2.connect(TEST_URL)
        cls.conn.autocommit = True
        from src.db import schema as schema_module

        with _test_db_cursors(cls.conn):
            schema_module.initialize_schema()
            schema_module.initialize_schema()  # repeatable

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        self.links = []

    def tearDown(self):
        if not self.links:
            return
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM events WHERE apply_link LIKE 'itest-%%'")
            cur.execute("DELETE FROM scraped_data WHERE url LIKE 'itest-%%'")

    def _new_link(self):
        link = _tag()
        self.links.append(link)
        return link

    def _event_id(self, link):
        with self.conn.cursor() as cur:
            cur.execute("SELECT id FROM events WHERE apply_link = %s", (link,))
            return cur.fetchone()[0]

    def _igs(self, event_id):
        with self.conn.cursor() as cur:
            cur.execute("SELECT ig_name FROM event_interest_groups "
                        "WHERE event_id = %s ORDER BY 1", (event_id,))
            return [r[0] for r in cur.fetchall()]

    def _deliveries(self, event_id):
        with self.conn.cursor() as cur:
            cur.execute("SELECT ig_name, channel FROM event_deliveries "
                        "WHERE event_id = %s ORDER BY 1, 2", (event_id,))
            return list(cur.fetchall())

    def _upsert(self, link, tags):
        from src.db.repositories.event_repository import EventRepository

        with _test_db_cursors(self.conn):
            return EventRepository.upsert({
                "title": "itest", "ig": tags[0], "ig_names": list(tags),
                "category": "News", "summary": "s", "apply_link": link,
                "validity_score": 8,
            })

    def _top(self, ig, category, **kw):
        from src.db.postgres_database import DatabaseFacade

        with _test_db_cursors(self.conn):
            return DatabaseFacade.get_top_opportunities_by_ig_and_category(
                _bare_facade(), ig, category, limit=5, **kw)

    def test_multi_ig_insert_no_duplicates(self):
        link = self._new_link()
        eid = self._upsert(link, ["AI", "Web Development"])
        self._upsert(link, ["AI", "Web Development", "AI"])
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM events WHERE apply_link = %s",
                        (link,))
            self.assertEqual(cur.fetchone()[0], 1)
        self.assertEqual(self._igs(eid), ["AI", "Web Development"])

    def test_update_removes_stale_memberships(self):
        link = self._new_link()
        eid = self._upsert(link, ["AI", "Web Development"])
        self._upsert(link, ["AI"])
        self.assertEqual(self._igs(eid), ["AI"])

    def test_legacy_single_ig_compatible(self):
        link = self._new_link()
        eid = self._upsert(link, ["AI"])
        with self.conn.cursor() as cur:
            cur.execute("SELECT ig FROM events WHERE id = %s", (eid,))
            self.assertEqual(cur.fetchone()[0], "AI")
        rows = self._top("AI", "News")
        self.assertTrue(any(r["link"] == link for r in rows))

    def test_secondary_ig_retrieval(self):
        link = self._new_link()
        self._upsert(link, ["AI", "Web Development"])
        rows = self._top("Web Development", "News")
        self.assertTrue(any(r["link"] == link for r in rows))
        # No duplicate canonical rows for one event.
        self.assertEqual(sum(1 for r in rows if r["link"] == link), 1)

    def test_pending_excludes_only_delivered_combo(self):
        from src.db.deliveries import record_deliveries

        link = self._new_link()
        eid = self._upsert(link, ["AI", "Web Development"])
        with self.conn.cursor() as cur:
            record_deliveries(cur, [eid], "AI", "zulip")
        ai = self._top("AI", "News", pending_ig="AI", pending_channel="zulip")
        web = self._top("Web Development", "News",
                        pending_ig="Web Development", pending_channel="zulip")
        mail = self._top("AI", "News", pending_ig="AI", pending_channel="email")
        self.assertFalse(any(r["link"] == link for r in ai))
        self.assertTrue(any(r["link"] == link for r in web))
        self.assertTrue(any(r["link"] == link for r in mail))

    def test_partial_failure_leaves_pending(self):
        link = self._new_link()
        eid = self._upsert(link, ["AI", "Web Development"])
        self.assertEqual(self._deliveries(eid), [])
        self.assertEqual(self._igs(eid), ["AI", "Web Development"])

    def test_conservative_backfill(self):
        link = self._new_link()
        eid = self._upsert(link, ["AI", "Web Development"])
        with self.conn.cursor() as cur:
            cur.execute("UPDATE events SET zulip_sent = TRUE WHERE id = %s",
                        (eid,))
        from src.db.deliveries import ensure_delivery_schema
        with _test_db_cursors(self.conn):
            from src.db.connection import db_conn
            with db_conn.get_cursor() as cur:
                ensure_delivery_schema(cur)
                ensure_delivery_schema(cur)  # repeatable
        got = [(g, c) for g, c in self._deliveries(eid)]
        self.assertIn(("AI", "zulip"), got)
        self.assertNotIn(("Web Development", "zulip"), got)

    def test_rerun_no_duplication(self):
        link = self._new_link()
        eid = self._upsert(link, ["AI", "Web Development"])
        from src.db import schema as schema_module

        with _test_db_cursors(self.conn):
            schema_module.initialize_schema()
        self.assertEqual(self._igs(eid), ["AI", "Web Development"])
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM events WHERE apply_link = %s",
                        (link,))
            self.assertEqual(cur.fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
