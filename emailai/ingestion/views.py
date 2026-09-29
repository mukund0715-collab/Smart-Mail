import imaplib
import email
from email.header import decode_header
from email.utils import parsedate_to_datetime
import uuid
import logging
from django.conf import settings
from django.utils import timezone
from celery import shared_task
from core.models import EmailRecord
from processing.tasks import process_email_pipeline

logger = logging.getLogger(__name__)

def extract_body(msg):
    """Extracts the plain text body from a MIME email payload."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode(errors="replace")
    else:
        return msg.get_payload(decode=True).decode(errors="replace")
    return ""

def decode_mime_header(header_val):
    if not header_val:
        return ""
    decoded_parts = decode_header(header_val)
    return "".join([
        str(t[0], t[1] or "utf-8", errors="replace") if isinstance(t[0], bytes) else t[0]
        for t in decoded_parts
    ])

@shared_task(queue="io_fast_queue")
def fetch_unseen_imap_emails():
    """
    Connects to IMAP, fetches UNSEEN messages, parses them, 
    saves to PostgreSQL, and dispatches the processing pipeline.
    """
    try:
        mail = imaplib.IMAP4_SSL(settings.IMAP_HOST)
        mail.login(settings.IMAP_USER, settings.IMAP_PASSWORD)
        mail.select("INBOX")
        
        # Search for unread emails
        status, response = mail.search(None, "UNSEEN")
        if status != "OK":
            return
            
        message_ids = response[0].split()
        
        for num in message_ids:
            # Fetch the raw RFC822 message payload
            typ, data = mail.fetch(num, "(RFC822)")
            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email)
            
            # 1. Fallback for Message-ID if missing
            msg_id = msg.get("Message-ID")
            if not msg_id:
                msg_id = f"<{uuid.uuid4()}@local.fallback>"
                
            # 2. Extract and decode headers
            subject = decode_mime_header(msg.get("Subject"))
            sender = decode_mime_header(msg.get("From"))
            
            date_str = msg.get("Date")
            try:
                received_at = parsedate_to_datetime(date_str)
            except (TypeError, ValueError):
                received_at = timezone.now()
                
            # 3. Extract body and compile headers for raw_payload
            body = extract_body(msg)
            headers_dict = dict(msg.items())
            
            # 4. Save to Database
            email_record, created = EmailRecord.objects.get_or_create(
                message_id=msg_id,
                defaults={
                    "sender_email": sender,
                    "subject": subject,
                    "received_at": received_at,
                    "raw_body": body,
                    "raw_payload": headers_dict,
                },
            )
            
            if created:
                # Dispatch the existing pipeline by PK
                process_email_pipeline.delay(email_record.id)
                
            # Only mark as read (\\Seen) after successful DB commit
            mail.store(num, "+FLAGS", "\\Seen")
            
        mail.logout()
        
    except Exception as exc:
        logger.error(f"IMAP Fetch failed: {exc}")