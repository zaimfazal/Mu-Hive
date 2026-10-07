#!/usr/bin/env python3
"""
scripts/zulip_notify.py
================-------
Production Zulip Notification Agent.
Only reads processed, high-quality items from the database.
"""

import asyncio
import logging
import sys
import os
from datetime import datetime
from dotenv import load_dotenv
import psycopg2

# Load environment variables
load_dotenv()

# Fix path for imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.db.postgres_database import db
from src.db.deliveries import ensure_delivery_schema, record_deliveries, CHANNEL_ZULIP
from scripts.zulip_writer import ZulipWriter
from src.config.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

# --- Helper Functions ---

def get_zulip_credentials(ig_name: str) -> dict:
    slug = ig_name.lower().replace(" ", "_").replace("/", "_").upper()
    email = os.getenv(f"ZULIP_{slug}_EMAIL")
    key = os.getenv(f"ZULIP_{slug}_KEY")
    site = os.getenv(f"ZULIP_{slug}_SITE", "https://mulearn.zulipchat.com/")
    if email and key:
        return {"email": email, "api_key": key, "site": site}
    return None

def format_date(date_str: str) -> str:
    if not date_str or date_str.lower() == "tba" or date_str == "N/A":
        return "TBA"
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return dt.strftime("%B %d, %Y")
    except:
        return date_str

CHANNEL_MAP = {
    "AI": "AI IG",
    "Cyber Security": "Cyber Security IG",
    "Web Development": "Web Dev IG",
    "Data Science": "Data Science IG",
    "UI/UX": "UI UX IG",
}


def resolve_zulip_channels():
    """Merge registry channels with the legacy CHANNEL_MAP override.

    Precedence: explicit CHANNEL_MAP entries win over same-named registry
    entries (currently identical for the original five IGs). Returns an
    ordered {ig_name: channel} dict covering every active IG that has a
    configured channel. IGs without channels are absent (skipped, unmarked).
    """
    from src.config.interest_groups import registry

    try:
        merged = registry.get_zulip_channels()
    except Exception:
        merged = {}
    merged.update(CHANNEL_MAP)
    return merged

def build_digest_body(items: list, is_news: bool = False) -> str:
    body = ""
    for item in items:
        title = item.get('title', 'Unknown')
        link = item.get('url', '')
        # Data is stored in the 'data' JSONB column in Postgres
        json_data = item.get('data') or {}
        summary = item.get('summary') or json_data.get('summary') or 'No summary available.'
        
        fmt = f"* **{title}**\n  *Summary*: {summary}\n  *Link*: [Read More]({link})\n"
        
        if not is_news:
            mode = json_data.get('location', 'Online')
            deadline = format_date(json_data.get('endDate', 'TBA'))
            fmt = fmt.replace("[Read More]", "[Register Here]")
            fmt += f"  *Mode*: {mode}\n"
            fmt += f"  *Deadline*: {deadline}\n"
        
        body += fmt + "\n"
    return body

# --- Main Notification Function ---

async def run_zulip_notifications():
    """
    Main entry point for Johan's Notification Part.
    Queries the database for high-quality items and sends them to Zulip.
    """
    logger.info("📡 Starting Zulip Notification Agent...")

    # Open a dedicated connection for marking items as sent
    notify_conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    notify_conn.autocommit = True
    notify_cur = notify_conn.cursor()

    # Delivery log table (+ conservative backfill); idempotent.
    ensure_delivery_schema(notify_cur)

    from src.config.settings import MAX_IGS_PER_RUN, NOTIFY_PER_IG_LIMIT
    from src.config.interest_groups import apply_ig_cap, registry

    channels = resolve_zulip_channels()
    ig_list, skipped_igs = apply_ig_cap(list(channels.keys()), MAX_IGS_PER_RUN)
    stats = {
        "igs_configured": len(registry.all_active_names()),
        "igs_with_channels": len(channels),
        "igs_processed": 0,
        "igs_skipped_cap": len(skipped_igs),
        "igs_unconfigured": 0,
        "items_sent": 0,
    }
    if skipped_igs:
        logger.warning(
            "Zulip capped to %d/%d IGs (MAX_IGS_PER_RUN=%s); skipped: %s",
            len(ig_list), len(channels), MAX_IGS_PER_RUN, ", ".join(skipped_igs),
        )
    no_channel = sorted(set(registry.all_active_names()) - set(channels))
    if no_channel:
        logger.info(
            "IGs without Zulip channels (not deliverable, never marked): %s",
            ", ".join(no_channel),
        )
        stats["igs_unconfigured"] = len(no_channel)

    for ig_name in ig_list:
        zulip_channel = channels[ig_name]
        creds = get_zulip_credentials(ig_name)
        if not creds:
            logger.warning(f"⚠️ No credentials for {ig_name}. Skipping...")
            stats["igs_unconfigured"] += 1
            continue
            
        logger.info(f"🤖 Processing Notifications for {ig_name} -> #{zulip_channel}")
        writer = ZulipWriter(email=creds['email'], api_key=creds['api_key'], site=creds['site'])

        # Read from Database — fetch top 20 items pending for THIS IG.
        # Per-(event, IG) delivery rows (not the global flag) decide
        # eligibility, so one IG's send never suppresses another IG's.
        news_items = db.get_top_opportunities_by_ig_and_category(
            ig_name, "News", limit=NOTIFY_PER_IG_LIMIT, include_sent=False,
            pending_ig=ig_name, pending_channel=CHANNEL_ZULIP)
        hack_items = db.get_top_opportunities_by_ig_and_category(
            ig_name, "Hackathons", limit=NOTIFY_PER_IG_LIMIT, include_sent=False,
            pending_ig=ig_name, pending_channel=CHANNEL_ZULIP)
        # NOTE: no in-memory zulip_sent filter here. Eligibility is decided by
        # the per-(event, IG) pending query above; the legacy global
        # events.zulip_sent flag is maintained only for compatibility and must
        # not suppress other IGs in the same run.

        if not news_items and not hack_items:
            logger.info(f"ℹ️ No new items found in DB for {ig_name}.")
            continue

        news_body = build_digest_body(news_items, is_news=True)
        hacks_body = build_digest_body(hack_items, is_news=False)

        # Deliver to Zulip. Only IDs whose send succeeded are recorded;
        # a failed topic stays pending for the next run.
        sent_ids = []
        try:
            if news_body:
                writer.client.send_message({
                    "type": "stream", "to": zulip_channel, "topic": "📰 News & Insights",
                    "content": f"## {ig_name} News Digest\n---\n{news_body}"
                })
                sent_ids.extend([i['id'] for i in news_items])
            if hacks_body:
                writer.client.send_message({
                    "type": "stream", "to": zulip_channel, "topic": "🚀 Hackathons",
                    "content": f"## {ig_name} Hackathon Digest\n---\n{hacks_body}"
                })
                sent_ids.extend([i['id'] for i in hack_items])
        except Exception as delivery_err:
            logger.error(f"❌ Failed to deliver Zulip notifications for {ig_name}: {delivery_err}")

        if sent_ids:
            sent_ids = list(dict.fromkeys(sent_ids))
            # Per-(event, IG) delivery log first; legacy global flag for compat.
            record_deliveries(notify_cur, sent_ids, ig_name, CHANNEL_ZULIP)
            notify_cur.execute(
                "UPDATE events SET zulip_sent = TRUE WHERE id IN %s",
                (tuple(sent_ids),)
            )
            logger.info(f"✅ Notified #{zulip_channel} with {len(sent_ids)} items.")
            stats["igs_processed"] += 1
            stats["items_sent"] += len(sent_ids)

    notify_cur.close()
    notify_conn.close()
    logger.info(
        "🏁 Notification Task Complete: configured=%d with_channels=%d "
        "processed=%d unconfigured=%d sent=%d.",
        stats["igs_configured"], stats["igs_with_channels"],
        stats["igs_processed"], stats["igs_unconfigured"], stats["items_sent"],
    )
    return stats

if __name__ == "__main__":
    asyncio.run(run_zulip_notifications())
