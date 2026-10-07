import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import logging
import psycopg2
from psycopg2.extras import DictCursor
from dotenv import load_dotenv
from src.config.logging_config import setup_logging
from src.db.deliveries import ensure_delivery_schema, record_deliveries, CHANNEL_EMAIL

load_dotenv()
setup_logging()
logger = logging.getLogger(__name__)

def resolve_email_recipients(ig_mails_rows):
    """Union registry emails (base) with ig_mails DB rows (override).

    Keys are normalized to canonical IG names (same normalize-with-fallback
    behavior as before). A DB row always wins over the registry address for
    the same canonical IG. IGs with no destination are absent: callers must
    report them as skipped, never marked delivered.
    """
    from src.config.interest_groups import registry
    from src.utils.ig_normalizer import normalize_ig

    try:
        merged = registry.get_email_recipients()
    except Exception:
        merged = {}
    for record in ig_mails_rows or []:
        raw_ig = record['ig']
        email = record['email']
        ig = normalize_ig(raw_ig)
        if not ig:
            ig = raw_ig.strip() if raw_ig else None
        if ig and email:
            merged[ig] = email
    return merged


def send_email(subject, body, receiver):
    sender   = os.getenv("GMAIL_SENDER")
    password = os.getenv("GMAIL_APP_PASSWORD")

    message = MIMEMultipart()
    message["From"]    = sender
    message["To"]      = receiver
    message["Subject"] = subject
    message.attach(MIMEText(body, "html"))

    receivers_list = [email.strip() for email in receiver.split(",")]

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, receivers_list, message.as_string())
        logger.info("Sent email to %s for %s", receivers_list, subject)

def run_email_agent():
    logger.info("Email Agent starting...")

    # Verify SMTP credentials are configured
    sender = os.getenv("GMAIL_SENDER")
    password = os.getenv("GMAIL_APP_PASSWORD")
    if not sender or not password:
        logger.error(
            "GMAIL_SENDER or GMAIL_APP_PASSWORD not set in .env — cannot send emails."
        )
        return {
            "igs_configured": 0,
            "igs_with_recipients": 0,
            "igs_processed": 0,
            "igs_skipped_cap": 0,
            "igs_unconfigured": 0,
            "emails_sent": 0,
        }

    # Database connection
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.warning("DATABASE_URL not found in .env. Checking local defaults...")
        db_url = "dbname=mu_hive user=postgres password=postgres host=localhost"

    conn = None
    cursor = None
    emails_sent = 0

    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cursor = conn.cursor(cursor_factory=DictCursor)

        # Delivery log table (+ conservative backfill); idempotent.
        ensure_delivery_schema(cursor)

        # Fetch IG to email mappings (DB rows override registry addresses).
        cursor.execute("SELECT ig, email FROM ig_mails")
        ig_email_records = cursor.fetchall()

        from src.config.settings import MAX_IGS_PER_RUN, NOTIFY_PER_IG_LIMIT
        from src.config.interest_groups import apply_ig_cap, registry

        recipients = resolve_email_recipients(ig_email_records)
        ig_list, skipped_igs = apply_ig_cap(sorted(recipients.keys()), MAX_IGS_PER_RUN)
        stats = {
            "igs_configured": len(registry.all_active_names()),
            "igs_with_recipients": len(recipients),
            "igs_processed": 0,
            "igs_skipped_cap": len(skipped_igs),
            "igs_unconfigured": 0,
            "emails_sent": 0,
        }
        if not recipients:
            logger.warning(
                "No email destinations found in 'ig_mails' or the registry. "
                "Insert rows (ig, email) or configure registry emails to enable delivery."
            )
            return stats
        if skipped_igs:
            logger.warning(
                "Email capped to %d/%d IGs (MAX_IGS_PER_RUN=%s); skipped: %s",
                len(ig_list), len(recipients), MAX_IGS_PER_RUN, ", ".join(skipped_igs),
            )
        no_recipient = sorted(set(registry.all_active_names()) - set(recipients))
        if no_recipient:
            logger.info(
                "IGs without email recipients (not deliverable, never marked): %s",
                ", ".join(no_recipient),
            )
            stats["igs_unconfigured"] = len(no_recipient)

        logger.info("Found %d IG→email destinations (DB + registry).", len(recipients))

        for ig in ig_list:
            email = recipients[ig]

            # Fetch pending News for this IG: legacy primary IG or junction
            # membership, excluding only this (event, IG) email delivery.
            # The legacy global mail_sent flag is intentionally NOT consulted:
            # one IG's send must not suppress another IG's delivery.
            # (case-insensitive, ordered by validity_score, limit 20)
            cursor.execute(
                """
                SELECT DISTINCT e.id, e.category, e.summary, e.apply_link,
                    e.platform, e.location, e.deadline
                FROM events e
                LEFT JOIN event_interest_groups jig
                    ON jig.event_id = e.id AND LOWER(jig.ig_name) = LOWER(%s)
                WHERE (LOWER(e.ig) = LOWER(%s) OR jig.ig_name IS NOT NULL)
                    AND e.category = 'News'
                    AND NOT EXISTS (
                        SELECT 1 FROM event_deliveries d
                        WHERE d.event_id = e.id
                          AND d.ig_name = %s
                          AND d.channel = 'email'
                    )
                ORDER BY e.validity_score DESC, e.created_at DESC
                LIMIT %s
                """,
                (ig, ig, ig, NOTIFY_PER_IG_LIMIT)
            )
            news_events = cursor.fetchall()

            # Fetch pending Hackathons for this IG (same membership semantics)
            cursor.execute(
                """
                SELECT DISTINCT e.id, e.category, e.summary, e.apply_link,
                    e.platform, e.location, e.deadline
                FROM events e
                LEFT JOIN event_interest_groups jig
                    ON jig.event_id = e.id AND LOWER(jig.ig_name) = LOWER(%s)
                WHERE (LOWER(e.ig) = LOWER(%s) OR jig.ig_name IS NOT NULL)
                    AND e.category = 'Hackathons'
                    AND NOT EXISTS (
                        SELECT 1 FROM event_deliveries d
                        WHERE d.event_id = e.id
                          AND d.ig_name = %s
                          AND d.channel = 'email'
                    )
                ORDER BY e.validity_score DESC, e.created_at DESC
                LIMIT %s
                """,
                (ig, ig, ig, NOTIFY_PER_IG_LIMIT)
            )
            hack_events = cursor.fetchall()

            events = news_events + hack_events

            if not events:
                logger.info(
                    "[%s] No unsent events found (News: 0, Hackathons: 0). Skipping.", ig
                )
                continue

            logger.info(
                "[%s] Found %d unsent events (News: %d, Hackathons: %d). Building email for %s.",
                ig, len(events), len(news_events), len(hack_events), email,
            )

            # Build email body, grouping by category
            body_parts = []
            current_category = None
            for e in events:
                cat = str(e['category'] or "General").strip()
                if cat != current_category:
                    # start a new section
                    body_parts.append(f"<h2>{cat}</h2>")
                    current_category = cat
                summ = str(e['summary'] or "No summary provided.").replace("\n", "<br>").strip()
                link = str(e['apply_link'] or "No link available.").strip()
                link_label = "Apply link"
                extra = ""
                if cat.lower() == "hackathons":
                    platform = str(e['platform'] or "").strip()
                    location = str(e['location'] or "").strip()
                    deadline = str(e['deadline'] or "").strip()
                    if platform:
                         extra += f"<b>Platform:</b> {platform}<br>"
                    if location:
                         extra += f"<b>Location:</b> {location}<br>"
                    if deadline and deadline.lower() != "none":
                         extra += f"<b>Deadline:</b> {deadline}<br>"
                else:
                    link_label = "Read more"

                event_html = (
                    f"{extra}"
                    f"{summ}<br>"
                    f"{link_label}: <a href='{link}'>{link}</a><br><br>"
                )
                body_parts.append(event_html)

            # Join sections with a horizontal rule
            body = "<hr>".join(body_parts)

            # Send the email
            try:
                send_email(
                    subject=f"{ig.title()} Digest",
                    body=body,
                    receiver=email,
                )
                emails_sent += 1
            except smtplib.SMTPAuthenticationError as e:
                logger.error(
                    "SMTP authentication failed — check GMAIL_APP_PASSWORD in .env. "
                    "Error: %s", e,
                )
                raise  # Fatal — no point trying the remaining IGs
            except smtplib.SMTPException as e:
                logger.error(
                    "[%s] SMTP error sending to %s: %s. Continuing with next IG.",
                    ig, email, e,
                )
                continue

            # Mark events as sent: per-(event, IG) delivery log first (only
            # this IG; other IGs stay pending), then the legacy global flag
            # for backward compatibility. Runs only after a successful send.
            event_ids = tuple([e['id'] for e in events])
            if event_ids:
                record_deliveries(cursor, event_ids, ig, CHANNEL_EMAIL)
                cursor.execute(
                    "UPDATE events SET mail_sent = TRUE WHERE id IN %s",
                    (event_ids,)
                )
                logger.info("[%s] Marked %d events as mail_sent=TRUE.", ig, len(event_ids))
            stats["igs_processed"] += 1

    except psycopg2.Error as e:
        logger.error("Email Agent database error: %s", e)
        raise
    except smtplib.SMTPException:
        raise  # Already logged above
    except Exception as e:
        logger.error("Email Agent unexpected error: %s", e, exc_info=True)
        raise
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()

    stats["emails_sent"] = emails_sent
    logger.info(
        "Email Agent done. Sent %d digest email(s): configured=%d "
        "with_recipients=%d processed=%d unconfigured=%d.",
        emails_sent, stats["igs_configured"], stats["igs_with_recipients"],
        stats["igs_processed"], stats["igs_unconfigured"],
    )
    return stats

if __name__ == "__main__":
    run_email_agent()
