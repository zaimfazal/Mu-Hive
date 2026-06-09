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

# Load environment variables
load_dotenv()

# Fix path for imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.db.postgres_database import db
from scripts.zulip_writer import ZulipWriter
from src.config.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

# --- Helper Functions ---

def get_zulip_credentials(ig_name: str) -> dict:
    slug = ig_name.lower().replace(" ", "_").upper()
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
    "Web Development": "Web Dev IG"
}

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
    
    for ig_name, zulip_channel in CHANNEL_MAP.items():
        creds = get_zulip_credentials(ig_name)
        if not creds:
            logger.warning(f"⚠️ No credentials for {ig_name}. Skipping...")
            continue
            
        logger.info(f"🤖 Processing Notifications for {ig_name} -> #{zulip_channel}")
        writer = ZulipWriter(email=creds['email'], api_key=creds['api_key'], site=creds['site'])

        # Read from Database (Assuming Intelligence Agent has already processed them)
        news_items = db.get_top_opportunities_by_ig_and_category(ig_name, "News", limit=5)
        hack_items = db.get_top_opportunities_by_ig_and_category(ig_name, "Hackathons", limit=5)
        
        if not news_items and not hack_items:
            logger.info(f"ℹ️ No new items found in DB for {ig_name}.")
            continue

        news_body = build_digest_body(news_items, is_news=True)
        hacks_body = build_digest_body(hack_items, is_news=False)

        # Deliver to Zulip
        if news_body:
            writer.client.send_message({
                "type": "stream", "to": zulip_channel, "topic": "📰 News & Insights",
                "content": f"## {ig_name} News Digest\n---\n{news_body}"
            })
        if hacks_body:
            writer.client.send_message({
                "type": "stream", "to": zulip_channel, "topic": "🚀 Hackathons",
                "content": f"## {ig_name} Hackathon Digest\n---\n{hacks_body}"
            })

        logger.info(f"✅ Notified #{zulip_channel} with latest items from DB.")

    logger.info("🏁 Notification Task Complete.")

if __name__ == "__main__":
    asyncio.run(run_zulip_notifications())
