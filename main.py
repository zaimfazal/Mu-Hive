#!/usr/bin/env python3
"""
main.py
=======
Triggers the Zulip notification pipeline.
"""

from scripts.zulip_notify import run_zulip_notifications
from scripts.gmailsender import run_email_agent
from src.agents.intelligence import LLMFailureThresholdExceeded
from src.orchestrator import run_pipeline
from src.config.logging_config import setup_logging
import asyncio
import logging
import sys
import os

setup_logging()

sys.path.append(os.path.abspath(os.path.join(
    os.path.dirname(__file__), 'scripts')))


async def main():
    print("\n🌊 Mu-Hive Notification Agent Starting...")

    try:
        await run_pipeline()
    except LLMFailureThresholdExceeded:
        raise
    except Exception as e:
        print(f"❌ Pipeline failed: {e}")

    # Email agent — isolated so a failure doesn't block Zulip
    try:
        run_email_agent()
    except Exception as e:
        print(f"❌ Email sending failed: {e}")

    # Zulip notifications — isolated so a failure doesn't mask email results
    try:
        await run_zulip_notifications()
    except Exception as e:
        print(f"❌ Zulip notifications failed: {e}")

    print("🏁 Task Complete.\n")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped by user.")
    except LLMFailureThresholdExceeded as e:
        print(f"Crashed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Crashed: {e}")
