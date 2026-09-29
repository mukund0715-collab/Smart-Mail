"""
Phase 5: Dynamic priority synthesis — combines intent classification and
sentiment score into a single `priority_flag` used to route emails for
expedited human intervention.
"""
from typing import Set, Tuple

# Intents that are inherently urgent regardless of tone.
_ALWAYS_CRITICAL_INTENTS: Set[str] = {"payment_failure", "account_access"}

# Intents that only escalate when paired with strongly negative sentiment.
_CONDITIONALLY_CRITICAL_INTENTS: Set[str] = {"billing", "technical_support"}

# Sentiment below this threshold is treated as "highly negative".
_HIGHLY_NEGATIVE_THRESHOLD = -0.5


def calculate_priority(intent_category: str, sentiment_score: float) -> Tuple[bool, str]:
    """
    Returns (priority_flag, reason) so the reason can be logged/audited
    alongside the boolean without a separate lookup.
    """
    if intent_category in _ALWAYS_CRITICAL_INTENTS:
        return True, f"critical_intent:{intent_category}"

    if (
        intent_category in _CONDITIONALLY_CRITICAL_INTENTS
        and sentiment_score <= _HIGHLY_NEGATIVE_THRESHOLD
    ):
        return True, f"critical_intent_with_negative_sentiment:{intent_category}"

    return False, "standard"
