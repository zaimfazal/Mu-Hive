import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import logging
import psycopg2
from psycopg2.extras import DictCursor
from dotenv import load_dotenv
from src.config.logging_config import setup_logging

load_dotenv()
setup_logging()
logger = logging.getLogger(__name__)

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
        return

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

        # Fetch IG to email mappings
        cursor.execute("SELECT ig, email FROM ig_mails")
        ig_email_records = cursor.fetchall()

        if not ig_email_records:
            logger.warning(
                "No email mappings found in the 'ig_mails' table. "
                "Insert rows (ig, email) to enable email delivery."
            )
            return

        logger.info("Found %d IG→email mappings in ig_mails.", len(ig_email_records))

        for record in ig_email_records:
            raw_ig = record['ig']
            email = record['email']

            # Normalise IG name; fall back to the raw value when it is already
            # a canonical name that normalize_ig doesn't list as an alias.
            from src.utils.ig_normalizer import normalize_ig
            ig = normalize_ig(raw_ig)
            if not ig:
                # The raw value may already be a canonical IG name stored
                # directly in the events table — use it as-is.
                ig = raw_ig.strip()
                logger.info(
                    "IG '%s' not in normalizer alias list; using raw value '%s'.",
                    raw_ig, ig,
                )

            # Fetch unsent News for this IG (case-insensitive, ordered by validity_score, limit 20)
            cursor.execute(
                """
                SELECT id, category, summary, apply_link, platform, location, deadline
                FROM events
                WHERE LOWER(ig) = LOWER(%s)
                    AND category = 'News'
                    AND mail_sent = FALSE
                ORDER BY validity_score DESC, created_at DESC
                LIMIT 20
                """,
                (ig,)
            )
            news_events = cursor.fetchall()

            # Fetch unsent Hackathons for this IG (case-insensitive, ordered by validity_score, limit 20)
            cursor.execute(
                """
                SELECT id, category, summary, apply_link, platform, location, deadline
                FROM events
                WHERE LOWER(ig) = LOWER(%s)
                    AND category = 'Hackathons'
                    AND mail_sent = FALSE
                ORDER BY validity_score DESC, created_at DESC
                LIMIT 20
                """,
                (ig,)
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

            # Mark events as sent
            event_ids = tuple([e['id'] for e in events])
            if event_ids:
                cursor.execute(
                    "UPDATE events SET mail_sent = TRUE WHERE id IN %s",
                    (event_ids,)
                )
                logger.info("[%s] Marked %d events as mail_sent=TRUE.", ig, len(event_ids))

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

    logger.info("Email Agent done. Sent %d digest email(s).", emails_sent)

if __name__ == "__main__":
    run_email_agent()
