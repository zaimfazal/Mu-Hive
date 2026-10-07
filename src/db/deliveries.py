"""src/db/deliveries.py
======================
Per-(event, IG, channel) delivery tracking for Phase 4A.

The legacy event-level flags (events.zulip_sent / events.mail_sent) cannot
represent delivery to several Interest Groups: the first IG to send would
suppress the others. This module adds a normalized delivery log so every
associated IG is eligible independently, while the legacy flags are still
maintained for backward compatibility.

Backfill policy (conservative): a historical global sent flag proves delivery
to at most the legacy primary IG (events.ig) — the old code only ever
delivered an event to a single IG per run. The backfill therefore records
(event_id, events.ig, channel) only, and never fabricates deliveries for
secondary IGs. Those remain pending and will be delivered once.
"""

CHANNEL_ZULIP = "zulip"
CHANNEL_EMAIL = "email"


def ensure_delivery_schema(cur) -> None:
    """Create the delivery log table + backfill. Idempotent, repeatable.

    Rollback: DROP TABLE event_deliveries. The legacy global flags are
    untouched, so pre-4A behavior can be restored by reverting the notifier
    queries to the global-flag filter.
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS event_deliveries (
            event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
            ig_name TEXT NOT NULL,
            channel TEXT NOT NULL CHECK (channel IN ('zulip', 'email')),
            sent_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (event_id, ig_name, channel)
        );
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_event_deliveries_lookup
        ON event_deliveries (ig_name, channel);
        """
    )
    # Conservative backfill: global flag => delivered to the primary IG only.
    cur.execute(
        """
        INSERT INTO event_deliveries (event_id, ig_name, channel)
        SELECT id, ig, 'zulip' FROM events
        WHERE zulip_sent AND ig IS NOT NULL AND ig != ''
        ON CONFLICT (event_id, ig_name, channel) DO NOTHING;
        """
    )
    cur.execute(
        """
        INSERT INTO event_deliveries (event_id, ig_name, channel)
        SELECT id, ig, 'email' FROM events
        WHERE mail_sent AND ig IS NOT NULL AND ig != ''
        ON CONFLICT (event_id, ig_name, channel) DO NOTHING;
        """
    )


def record_deliveries(cur, event_ids, ig_name: str, channel: str) -> int:
    """Record successful per-IG deliveries. Idempotent; returns rows added.

    Call only after the corresponding send operation has succeeded.
    """
    ids = list(dict.fromkeys(event_ids or []))
    if not ids or not ig_name or channel not in (CHANNEL_ZULIP, CHANNEL_EMAIL):
        return 0
    added = 0
    for event_id in ids:
        cur.execute(
            "INSERT INTO event_deliveries (event_id, ig_name, channel) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (event_id, ig_name, channel) DO NOTHING;",
            (event_id, ig_name, channel),
        )
        added += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    return added
