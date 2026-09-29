from django.db import models
from encrypted_json_fields.fields import EncryptedTextField


class EmailRecord(models.Model):
    """
    The immutable origin record for an inbound email, as received from the
    webhook provider (SES / Mailgun / SendGrid). Nothing here is ever
    mutated after ingestion — downstream processing writes to the related
    EmailMetadata / ActionItem / GeneratedReply rows instead.
    """

    message_id = models.CharField(max_length=998, unique=True, db_index=True)
    sender_email = models.EmailField(db_index=True)
    subject = models.CharField(max_length=998, blank=True, default="")
    received_at = models.DateTimeField(db_index=True)

    # Encrypted at rest via pgcrypto-backed field. Holds the full,
    # un-redacted body exactly as received.
    raw_body = EncryptedTextField()

    # Raw provider payload for audit / replay, stored as JSON.
    raw_payload = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]
        indexes = [
            models.Index(fields=["sender_email", "received_at"]),
        ]

    def __str__(self):
        return f"{self.message_id} from {self.sender_email}"


class EmailMetadata(models.Model):
    """
    Derived, PII-scrubbed analysis of an EmailRecord: masked body, the
    reversible de-anonymization map, and the classification/priority
    results produced in Phases 4 & 5.
    """

    class IntentCategory(models.TextChoices):
        BILLING = "billing", "Billing"
        PAYMENT_FAILURE = "payment_failure", "Payment Failure"
        TECHNICAL_SUPPORT = "technical_support", "Technical Support"
        ACCOUNT_ACCESS = "account_access", "Account Access"
        FEATURE_REQUEST = "feature_request", "Feature Request"
        GENERAL_INQUIRY = "general_inquiry", "General Inquiry"
        SPAM = "spam", "Spam"
        OTHER = "other", "Other"

    email = models.OneToOneField(
        EmailRecord, on_delete=models.CASCADE, related_name="metadata"
    )

    intent_category = models.CharField(
        max_length=32, choices=IntentCategory.choices, blank=True, default=""
    )
    sentiment_score = models.FloatField(
        null=True, blank=True, help_text="Range -1.0 (very negative) to +1.0 (very positive)"
    )
    priority_flag = models.BooleanField(default=False, db_index=True)

    # PII -> synthetic-token mapping produced by the StatefulReversibleAnonymizer,
    # e.g. {"<PERSON_1>": "Jane Doe", "<PHONE_1>": "+1-555-0100"}.
    # Encrypted because it contains the original PII values.
    deanonymization_map = models.JSONField(default=dict, blank=True)

    # The scrubbed body sent onward to the LLM. Also encrypted since it
    # may still contain sensitive business context even without direct PII.
    masked_body = EncryptedTextField(blank=True, default="")

    processed_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Metadata for {self.email.message_id}"


class ActionItem(models.Model):
    """
    A discrete follow-up task extracted from an email by the Gemini
    structured-extraction step (Phase 6). One email can produce many.
    """

    email = models.ForeignKey(
        EmailRecord, on_delete=models.CASCADE, related_name="action_items"
    )
    task_description = models.TextField()
    deadline = models.DateTimeField(null=True, blank=True)
    is_completed = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["deadline", "created_at"]

    def __str__(self):
        return self.task_description[:80]


class GeneratedReply(models.Model):
    """
    The AI-drafted reply for an email, after de-anonymization has restored
    real names/contact details in place of synthetic tokens.
    """
    email = models.OneToOneField(
        EmailRecord, on_delete=models.CASCADE, related_name="generated_reply"
    )
    summary = models.TextField(blank=True, default="")
    draft_body = models.TextField(blank=True, default="")
    approved = models.BooleanField(default=False)
    
    # NEW: Tracking fields for the frontend outbound dispatch
    is_sent = models.BooleanField(default=False, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Draft reply for {self.email.message_id}"
