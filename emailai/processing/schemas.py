"""
Phase 6: Strict Pydantic schemas passed to Gemini's response_schema so the
model is constrained to valid JSON matching this exact shape — no
freeform prose, no missing fields.
"""
from typing import List, Optional

from pydantic import BaseModel, Field


class ActionItemSchema(BaseModel):
    task_description: str = Field(
        ..., description="A concise, actionable description of a follow-up task."
    )
    deadline_iso8601: Optional[str] = Field(
        None,
        description=(
            "ISO-8601 datetime for the deadline if one is stated or clearly "
            "implied in the email, otherwise null."
        ),
    )


class EmailProcessingResponse(BaseModel):
    summary: str = Field(
        ..., description="A one- to two-sentence neutral summary of the email's content."
    )
    action_items: List[ActionItemSchema] = Field(
        default_factory=list,
        description="Every distinct follow-up task implied by the email, if any.",
    )
    draft_reply: str = Field(
        ...,
        description=(
            "A professional draft reply to the sender. Must preserve any "
            "synthetic placeholder tokens (e.g. <PERSON_1>, <PHONE_1>) "
            "exactly as given, without altering brackets or inventing real "
            "names/details in their place."
        ),
    )
