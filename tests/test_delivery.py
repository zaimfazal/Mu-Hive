"""tests/test_delivery.py
========================
Per-(event, IG, channel) notification delivery (Phase 4A).

Offline: senders (Zulip client, send_email) are mocked and all database
access runs against an in-memory FakeCursor. No real messages are sent.
There is no configured test database in this repo, so round trips run
against the fake; the real retrieval SQL is additionally asserted to
contain the junction + delivery clauses. Real-Postgres correctness is
NOT claimed.
"""

import asyncio
import os
import smtplib
import sys
import unittest
from unittest.mock import Mock, patch

# The Zulip client package is not required to test delivery logic; stub it
# so scripts.zulip_notify imports offline (sender is always mocked).
sys.modules.setdefault("zulip", Mock())


def _run(coro):
    # Loop-state tolerant: asyncio.run() tears down the thread's current
    # loop, which breaks suites using asyncio.get_event_loop() when they run
    # after this file. Reuse (or create) a persistent loop instead.
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


class FakeState:
    def __init__(self):
        self.events = {}  # id -> dict row
        self.next_id = 1
        self.igs = set()  # (event_id, ig_name)
        self.deliveries = set()  # (event_id, ig_name, channel)
        self.ig_mails = []  # [{"ig":..., "email":...}]
        self.statements = []


def add_event(state, title, ig, category, score, link=None, igs=()):
    eid = state.next_id
    state.next_id += 1
    state.events[eid] = {
        "id": eid, "title": title, "ig": ig, "category": category,
        "summary": f"Summary of {title}", "apply_link": link or f"https://x/{eid}",
        "url": link or f"https://x/{eid}", "validity_score": score,
        "platform": "RSS", "location": "Online", "deadline": None,
        "days_left": None, "zulip_sent": False, "mail_sent": False,
        "created_at": eid,
    }
    state.igs.add((eid, ig))
    for extra in igs:
        state.igs.add((eid, extra))
    return eid


class FakeCursor:
    def __init__(self, state):
        self.state = state
        self._rows = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def close(self):
        pass

    def execute(self, sql, params=None):
        params = params or ()
        self.state.statements.append((sql, tuple(params)))
        s = " ".join(sql.split())
        st = self.state
        if "INSERT INTO event_deliveries" in s and "SELECT id, ig" in s:
            # Conservative backfill from legacy global flags (primary IG only).
            for eid, row in st.events.items():
                if "'zulip'" in s and row["zulip_sent"] and row["ig"]:
                    st.deliveries.add((eid, row["ig"], "zulip"))
                if "'email'" in s and row["mail_sent"] and row["ig"]:
                    st.deliveries.add((eid, row["ig"], "email"))
            self.rowcount = 0
        elif s.startswith("INSERT INTO event_deliveries"):
            key = (params[0], params[1], params[2])
            if key in st.deliveries:
                self.rowcount = 0
            else:
                st.deliveries.add(key)
                self.rowcount = 1
        elif s.startswith("SELECT ig, email FROM ig_mails"):
            self._rows = [dict(r) for r in st.ig_mails]
        elif "FROM events e" in s and "event_deliveries" in s:
            # Gmail pending query mirror: (legacy OR junction) AND category
            # AND no delivery row for (event, ig, email).
            ig = params[0]
            category = "News" if "'News'" in s else "Hackathons"
            out = []
            for eid, row in st.events.items():
                if row["category"] != category:
                    continue
                legacy = (row["ig"] or "").lower() == ig.lower()
                secondary = any(e == eid and g.lower() == ig.lower()
                                for (e, g) in st.igs)
                if not (legacy or secondary):
                    continue
                if (eid, ig, "email") in st.deliveries:
                    continue
                out.append({
                    "id": eid, "category": row["category"],
                    "summary": row["summary"], "apply_link": row["apply_link"],
                    "platform": row["platform"], "location": row["location"],
                    "deadline": row["deadline"],
                    "_score": row["validity_score"], "_created": row["created_at"],
                })
            out.sort(key=lambda r: (-r["_score"], r["_created"]))
            for r in out:
                r.pop("_score", None)
                r.pop("_created", None)
            self._rows = out[:20]
        elif s.startswith("UPDATE events SET mail_sent"):
            for eid in params[0]:
                if eid in st.events:
                    st.events[eid]["mail_sent"] = True
            self.rowcount = len(params[0])
        elif s.startswith("UPDATE events SET zulip_sent"):
            for eid in params[0]:
                if eid in st.events:
                    st.events[eid]["zulip_sent"] = True
            self.rowcount = len(params[0])
        else:
            # CREATE TABLE / INDEX / anything else: tolerated.
            self.rowcount = 0

    def fetchone(self):
        return None

    def fetchall(self):
        rows = self._rows
        self._rows = []
        return rows


class FakeConn:
    def __init__(self, state):
        self.state = state
        self.autocommit = True

    def cursor(self, cursor_factory=None):
        return FakeCursor(self.state)

    def close(self):
        pass


def _zulip_env():
    return {
        "ZULIP_AI_EMAIL": "ai-bot@example.com",
        "ZULIP_AI_KEY": "key-ai",
        "ZULIP_WEB_DEVELOPMENT_EMAIL": "web-bot@example.com",
        "ZULIP_WEB_DEVELOPMENT_KEY": "key-web",
    }


def _gmail_env():
    return {"GMAIL_SENDER": "sender@example.com", "GMAIL_APP_PASSWORD": "pw"}


def _zulip_row(row):
    return {
        "id": row["id"], "title": row["title"], "url": row["url"],
        "summary": row["summary"],
        "data": {"location": row["location"], "endDate": "TBA"},
        "zulip_sent": row["zulip_sent"],
    }


def _fake_zulip_get_top(state):
    def _get(ig, category, limit=5, include_sent=True,
             pending_ig=None, pending_channel=None):
        out = []
        for eid, row in state.events.items():
            if row["category"] != category:
                continue
            if not (row["ig"] == ig or (eid, ig) in state.igs):
                continue
            if (pending_ig and pending_channel
                    and (eid, pending_ig, pending_channel) in state.deliveries):
                continue
            out.append(_zulip_row(row))
        out.sort(key=lambda r: r["id"])
        return out[:limit]
    return _get


class DeliveryTestBase(unittest.TestCase):
    def make_state(self):
        state = FakeState()
        eid = add_event(state, "Multi IG Event", "AI", "News", 9,
                        igs=("Web Development",))
        return state, eid


class TestZulipPerIGDelivery(DeliveryTestBase):
    def _run_zulip(self, state, send_side_effect=None, env=None, clear_env=True):
        import scripts.zulip_notify as zn

        sends = []

        def _send(message):
            sends.append((message["to"], message["topic"]))
            if send_side_effect:
                send_side_effect(message)
            return {"id": len(sends)}

        writer = Mock()
        writer.client.send_message = Mock(side_effect=_send)
        # clear_env=True (default) isolates from the developer's real .env so
        # unconfigured destinations stay unconfigured deterministically.
        with patch("scripts.zulip_notify.psycopg2") as mock_pg, \
             patch("scripts.zulip_notify.ZulipWriter", return_value=writer), \
             patch.object(zn.db, "get_top_opportunities_by_ig_and_category",
                          create=True,
                          side_effect=_fake_zulip_get_top(state)), \
             patch.dict(os.environ, env if env is not None else _zulip_env(),
                        clear=clear_env):
            mock_pg.connect.return_value = FakeConn(state)
            _run(zn.run_zulip_notifications())
        return sends

    def test_two_igs_both_delivered(self):
        state, eid = self.make_state()
        sends = self._run_zulip(state)
        channels = {c for c, _ in sends}
        self.assertIn("AI IG", channels)
        self.assertIn("Web Dev IG", channels)
        self.assertIn((eid, "AI", "zulip"), state.deliveries)
        self.assertIn((eid, "Web Development", "zulip"), state.deliveries)
        self.assertTrue(state.events[eid]["zulip_sent"])  # legacy compat
        self.assertFalse(state.events[eid]["mail_sent"])  # other channel untouched

    def test_partial_failure_retry_sends_only_pending(self):
        state, eid = self.make_state()

        def _fail_web(message):
            if message["to"] == "Web Dev IG":
                raise Exception("zulip down")

        sends = self._run_zulip(state, send_side_effect=_fail_web)
        self.assertIn((eid, "AI", "zulip"), state.deliveries)
        self.assertNotIn((eid, "Web Development", "zulip"), state.deliveries)

        sends2 = self._run_zulip(state)
        targets = [c for c, _ in sends2]
        self.assertNotIn("AI IG", targets)  # no resend of success
        self.assertIn("Web Dev IG", targets)
        self.assertIn((eid, "Web Development", "zulip"), state.deliveries)
        self.assertEqual(len(sends) + len(sends2), 3)  # AI + failed Web + retry Web

    def test_rerun_sends_nothing(self):
        state, _ = self.make_state()
        self._run_zulip(state)
        sends2 = self._run_zulip(state)
        self.assertEqual(sends2, [])

    def test_unconfigured_destination_not_marked(self):
        state, eid = self.make_state()
        sends = self._run_zulip(
            state,
            env={"ZULIP_AI_EMAIL": "a@x", "ZULIP_AI_KEY": "k"},
            clear_env=True,
        )
        channels = {c for c, _ in sends}
        self.assertIn("AI IG", channels)
        self.assertNotIn("Web Dev IG", channels)
        self.assertIn((eid, "AI", "zulip"), state.deliveries)
        self.assertNotIn((eid, "Web Development", "zulip"), state.deliveries)


class TestEmailPerIGDelivery(DeliveryTestBase):
    def _run_email(self, state, send_side_effect=None):
        import scripts.gmailsender as gm

        calls = []

        def _send(subject, body, receiver):
            calls.append((subject, receiver, body))
            if send_side_effect:
                send_side_effect(subject, body, receiver)

        with patch("smtplib.SMTP"), \
             patch("scripts.gmailsender.psycopg2") as mock_pg, \
             patch("scripts.gmailsender.send_email", side_effect=_send), \
             patch.dict(os.environ, _gmail_env(), clear=False):
            mock_pg.connect.return_value = FakeConn(state)
            gm.run_email_agent()
        return calls

    def _seed_mails(self, state):
        state.ig_mails = [
            {"ig": "AI", "email": "ai@example.com"},
            {"ig": "Web Development", "email": "web@example.com"},
        ]

    def test_two_igs_both_delivered_digest_preserved(self):
        state, eid = self.make_state()
        add_event(state, "AI News 2", "AI", "News", 8)
        add_event(state, "AI News 3", "AI", "News", 7)
        add_event(state, "Web Hack 1", "Web Development", "Hackathons", 9)
        self._seed_mails(state)
        calls = self._run_email(state)
        by_receiver = {r: (s, b) for s, r, b in calls}
        self.assertIn("ai@example.com", by_receiver)
        self.assertIn("web@example.com", by_receiver)
        # Digest grouping preserved: category sections in one email per IG.
        _, ai_body = by_receiver["ai@example.com"]
        self.assertIn("<h2>News</h2>", ai_body)
        self.assertIn("Multi IG Event", ai_body)
        _, web_body = by_receiver["web@example.com"]
        self.assertIn("Multi IG Event", web_body)
        self.assertIn("Web Hack 1", web_body)
        self.assertIn((eid, "AI", "email"), state.deliveries)
        self.assertIn((eid, "Web Development", "email"), state.deliveries)
        self.assertTrue(state.events[eid]["mail_sent"])
        self.assertFalse(state.events[eid]["zulip_sent"])

    def test_partial_failure_retry_sends_only_pending(self):
        state, eid = self.make_state()
        self._seed_mails(state)

        def _fail_web(subject, body, receiver):
            if receiver == "web@example.com":
                raise smtplib.SMTPException("smtp down")

        self._run_email(state, send_side_effect=_fail_web)
        self.assertIn((eid, "AI", "email"), state.deliveries)
        self.assertNotIn((eid, "Web Development", "email"), state.deliveries)

        calls2 = self._run_email(state)
        receivers = [r for _, r, _ in calls2]
        self.assertEqual(receivers, ["web@example.com"])
        self.assertIn((eid, "Web Development", "email"), state.deliveries)

    def test_unconfigured_ig_not_marked(self):
        # Space has no registry email and no ig_mails row: not deliverable.
        state = FakeState()
        eid = add_event(state, "AI Space Event", "AI", "News", 9, igs=("Space",))
        state.ig_mails = [{"ig": "AI", "email": "ai@example.com"}]
        calls = self._run_email(state)
        self.assertEqual([r for _, r, _ in calls], ["ai@example.com"])
        self.assertIn((eid, "AI", "email"), state.deliveries)
        self.assertNotIn((eid, "Space", "email"), state.deliveries)

    def test_duplicate_associations_no_duplicate_notifications(self):
        state, eid = self.make_state()
        state.igs.add((eid, "Web Development"))  # duplicate membership row
        self._seed_mails(state)
        calls = self._run_email(state)
        web_bodies = [b for _, r, b in calls if r == "web@example.com"]
        self.assertEqual(len(web_bodies), 1)
        self.assertEqual(web_bodies[0].count("Multi IG Event"), 1)
        # Rerun: nothing pending, nothing sent.
        calls2 = self._run_email(state)
        self.assertEqual(calls2, [])


class TestDeliverySchema(unittest.TestCase):
    def test_get_top_pending_sql(self):
        from src.db.postgres_database import DatabaseFacade

        state = FakeState()

        class CannedCursor(FakeCursor):
            def fetchall(self):
                return []

        from src.db.connection import db_conn
        with patch.object(db_conn, "get_cursor",
                          side_effect=lambda factory=None: CannedCursor(state)):
            db = DatabaseFacade.__new__(DatabaseFacade)
            db._orchestrator_since, db._orchestrator_until = None, None
            db.get_top_opportunities_by_ig_and_category(
                "AI", "News", limit=5, include_sent=False,
                pending_ig="AI", pending_channel="zulip")
        text = " ".join(sql for sql, _ in state.statements
                        if "DISTINCT ON" in sql)
        self.assertIn("event_deliveries", text)
        self.assertIn("NOT EXISTS", text)
        self.assertNotIn("zulip_sent = FALSE", text)
        params = [p for sql, p in state.statements if "DISTINCT ON" in sql][0]
        # (junction ig, legacy ig, category, pending ig, pending channel, limit)
        self.assertEqual(params[:5],
                         ("AI", "AI", "News", "AI", "zulip"))

    def test_backfill_is_conservative(self):
        from src.db.connection import db_conn
        from src.db.deliveries import ensure_delivery_schema

        state = FakeState()
        eid = add_event(state, "Old Event", "AI", "News", 9,
                        igs=("Web Development",))
        state.events[eid]["zulip_sent"] = True  # legacy: sent somewhere once
        with patch.object(db_conn, "get_cursor",
                          side_effect=lambda factory=None: FakeCursor(state)):
            with db_conn.get_cursor() as cur:
                ensure_delivery_schema(cur)
        # Primary IG backfilled; secondary IG stays pending (never proven sent).
        self.assertIn((eid, "AI", "zulip"), state.deliveries)
        self.assertNotIn((eid, "Web Development", "zulip"), state.deliveries)
        self.assertNotIn((eid, "AI", "email"), state.deliveries)


if __name__ == "__main__":
    unittest.main()
