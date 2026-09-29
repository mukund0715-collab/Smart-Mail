import asyncio
import logging
from datetime import datetime

from celery import chain, shared_task
from django.utils import timezone

from core.models import ActionItem, EmailMetadata, EmailRecord, GeneratedReply

from .anonymizer import anonymize_text, restore_original_values
from .llm_gemini import extract_and_draft_reply
from .nlp_intent import classify_intent
from .nlp_sentiment import score_sentiment
from .priority import calculate_priority

logger = logging.getLogger(__name__)


@shared_task(name="processing.tasks.process_email_pipeline")
def process_email_pipeline(email_id: int):
    """
    Entry point dispatched from the webhook view with only a primary key.
    Kicks off the rest of the pipeline as a Celery chain so each stage is
    routed to its own queue (see settings.CELERY_TASK_ROUTES):

        anonymize_email (cpu_heavy_queue)
          -> classify_intent_and_sentiment (cpu_heavy_queue)
          -> generate_reply_with_gemini (api_bound_queue)
    """
    pipeline = chain(
        anonymize_email.s(email_id),
        classify_intent_and_sentiment.s(),
        generate_reply_with_gemini.s(),
    )
    pipeline.apply_async()


@shared_task(name="processing.tasks.anonymize_email")
def anonymize_email(email_id: int) -> int:
    """Phase 4: mask PII in the subject+body, store the reversible map."""
    email = EmailRecord.objects.get(id=email_id)

    combined_text = f"{email.subject}\n\n{email.raw_body}"
    masked_text, deanonymization_map = anonymize_text(combined_text)

    EmailMetadata.objects.update_or_create(
        email=email,
        defaults={
            "masked_body": masked_text,
            "deanonymization_map": deanonymization_map,
        },
    )

    return email_id


@shared_task(name="processing.tasks.classify_intent_and_sentiment")
def classify_intent_and_sentiment(email_id: int) -> int:
    """Phase 5: DistilBERT intent + RoBERTa sentiment -> priority flag."""
    metadata = EmailMetadata.objects.select_related("email").get(email_id=email_id)

    intent_label, _confidence = classify_intent(metadata.masked_body)
    sentiment = score_sentiment(metadata.masked_body)
    priority_flag, reason = calculate_priority(intent_label, sentiment)

    metadata.intent_category = intent_label
    metadata.sentiment_score = sentiment
    metadata.priority_flag = priority_flag
    metadata.save(update_fields=["intent_category", "sentiment_score", "priority_flag"])

    if priority_flag:
        logger.info("Email %s flagged priority (%s)", email_id, reason)

    return email_id


@shared_task(
    name="processing.tasks.generate_reply_with_gemini",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
)
def generate_reply_with_gemini(email_id: int) -> int:
    """
    Phase 6: send the masked email to Gemini for structured extraction +
    draft reply, then de-anonymize the result and commit everything.
    """
    email = EmailRecord.objects.get(id=email_id)
    metadata = email.metadata

    # The google-genai async client is called from inside a sync Celery
    # task; asyncio.run() gives it a clean event loop for the task's
    # lifetime without requiring the whole worker to run under asyncio.
    parsed = asyncio.run(
        extract_and_draft_reply(
            masked_subject=email.subject,
            masked_body=metadata.masked_body,
            intent=metadata.intent_category,
            sentiment=metadata.sentiment_score or 0.0,
        )
    )

    deanonymization_map = metadata.deanonymization_map
    restored_summary = restore_original_values(parsed.summary, deanonymization_map)
    restored_draft = restore_original_values(parsed.draft_reply, deanonymization_map)

    GeneratedReply.objects.update_or_create(
        email=email,
        defaults={
            "summary": restored_summary,
            "draft_body": restored_draft,
            "approved": False,
        },
    )

    # Replace any prior action items for this email rather than appending
    # duplicates on retry.
    ActionItem.objects.filter(email=email).delete()
    action_items = []
    for item in parsed.action_items:
        restored_description = restore_original_values(
            item.task_description, deanonymization_map
        )
        deadline = _parse_deadline(item.deadline_iso8601)
        action_items.append(
            ActionItem(email=email, task_description=restored_description, deadline=deadline)
        )
    ActionItem.objects.bulk_create(action_items)

    return email_id


def _parse_deadline(iso_string):
    if not iso_string:
        return None
    try:
        parsed = datetime.fromisoformat(iso_string)
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.utc)
        return parsed
    except ValueError:
        logger.warning("Could not parse deadline %r from Gemini output", iso_string)
        return None
