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
    
    # We fetch the exact Supabase string you placed in .env
    db_url = os.getenv("DATABASE_URL") 
    
    if not db_url:
        logger.warning("DATABASE_URL not found in .env. Checking local defaults...")
        db_url = "dbname=mu_hive user=postgres password=postgres host=localhost"

    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cursor = conn.cursor(cursor_factory=DictCursor)
        
        # 1. Fetch the IG to Email mappings right out of Postgres
        cursor.execute("SELECT ig, email FROM ig_mails")
        ig_email_records = cursor.fetchall()

        if not ig_email_records:
            logger.info("No email mappings found in the 'ig_mails' table.")

        for record in ig_email_records:
            ig = record['ig']
            email = record['email']

            # 2. Fetch structured events for this specific IG that haven't been emailed yet
            # ORDER BY category groups all events of the same category together
            cursor.execute(  """
                SELECT id, category, summary, apply_link
                FROM events
                WHERE LOWER(ig) = LOWER(%s)
                    AND mail_sent = FALSE
                ORDER BY category ASC
                """,
                (ig,)
            )
            events = cursor.fetchall()

            if not events:
                logger.info("[%s] No events, skipping.", ig)
                continue

            # Join formatted HTML strings together
            body_parts = []
            for e in events:
                cat = str(e['category'] or "General").strip()
                summ = str(e['summary'] or "No summary provided.").replace("\n", "<br>").strip()
                link = str(e['apply_link'] or "No link available.").strip()
                
                event_html = (
                    f"<b>CATEGORY : {cat}</b><br><br>"
                    f"{summ}<br><br>"
                    f"Apply link: <a href='{link}'>{link}</a>"
                )
                body_parts.append(event_html)

            # Separate multiple events using a professional horizontal rule
            body = "<br><hr><br>".join(body_parts)

            # Send Email
            send_email(
                subject=f"{ig.title()} Digest",
                body=body,
                receiver=email
            )

            # 3. Mark them as sent so we don't spam!
            event_ids = tuple([e['id'] for e in events])
            if event_ids:
                cursor.execute(
                    "UPDATE events SET mail_sent = TRUE WHERE id IN %s",
                    (event_ids,)
                )

    except Exception as e:
        logger.error("Email Agent PostgreSQL error: %s", e)
    finally:
        if 'cursor' in locals(): 
            cursor.close()
        if 'conn' in locals(): 
            conn.close()

    logger.info("Email Agent done.")

if __name__ == "__main__":
    run_email_agent()
