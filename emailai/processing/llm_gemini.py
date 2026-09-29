"""
Phase 6: Generative extraction & reply drafting.

The google-genai client is initialized once at module level within the
api_bound_queue worker process, matching the pattern used for the
Presidio/DistilBERT engines in the cpu_heavy_queue — avoid re-creating
network clients per task.
"""
import logging

from django.conf import settings
from google import genai
from google.genai import types

from .schemas import EmailProcessingResponse

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are an email-processing assistant operating ONLY on data that has \
already been scrubbed of personally identifiable information (PII).

Every name, email address, phone number, and other sensitive identifier \
in the input has been replaced with a synthetic placeholder token of the \
form <ENTITY_TYPE_N> (for example <PERSON_1>, <PHONE_1>, <EMAIL_ADDRESS_1>).

Strict rules you must follow:
1. Treat every placeholder token as an opaque reference. Never attempt to \
   guess, infer, or hallucinate the real value it stands for.
2. When a placeholder token appears in the input and is relevant to your \
   output (summary, action items, or draft reply), reproduce that exact \
   token verbatim, including the angle brackets and underscore-numbering. \
   Do not alter, renumber, translate, or reformat it in any way.
3. Do not introduce any new names, phone numbers, emails, or other \
   identifying details that were not already present as a placeholder \
   token in the input.
4. Write the draft reply in a professional, empathetic tone appropriate \
   to the email's intent and sentiment.

Respond only with JSON matching the provided schema.
"""

#_client = genai.Client(api_key=settings.GEMINI_API_KEY)

def get_genai_client():
    """Initialize the client only when needed."""
    if not settings.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not configured in Django settings.")
    return genai.Client(api_key=settings.GEMINI_API_KEY)

def _build_user_prompt(masked_subject: str, masked_body: str, intent: str, sentiment: float) -> str:
    return (
        f"Intent classification: {intent}\n"
        f"Sentiment score (-1.0 to 1.0): {sentiment}\n\n"
        f"Subject: {masked_subject}\n\n"
        f"Body:\n{masked_body}"
    )


async def extract_and_draft_reply(
    masked_subject: str, masked_body: str, intent: str, sentiment: float
) -> EmailProcessingResponse:
    """
    Calls Gemini asynchronously with a strict response_schema so the
    result can be parsed directly into EmailProcessingResponse without
    manual JSON validation.
    """
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_schema=EmailProcessingResponse,
        temperature=settings.GEMINI_TEMPERATURE,
    )

    client = get_genai_client()
    response = await client.aio.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=_build_user_prompt(masked_subject, masked_body, intent, sentiment),
        config=config,
    )

    parsed = response.parsed
    if parsed is None:
        # Defensive fallback: response_schema should make this unreachable,
        # but never silently swallow a malformed response.
        logger.error("Gemini returned no parsed structured output: %r", response.text)
        raise ValueError("Gemini response did not match EmailProcessingResponse schema")

    return parsed
