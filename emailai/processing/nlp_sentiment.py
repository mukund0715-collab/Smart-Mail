"""
Phase 5: Sentiment extraction via the lightweight
cardiffnlp/twitter-roberta-base-sentiment-latest model, tuned to pick up
implicit tone (frustration, urgency) rather than just keyword polarity.
"""
from functools import lru_cache

from django.conf import settings

# Model outputs 3 classes in this fixed order.
_LABEL_TO_SCORE = {"negative": -1.0, "neutral": 0.0, "positive": 1.0}


@lru_cache(maxsize=1)
def _load_pipeline():
    from transformers import pipeline

    return pipeline(
        "sentiment-analysis",
        model=settings.SENTIMENT_MODEL_NAME,
        tokenizer=settings.SENTIMENT_MODEL_NAME,
        top_k=None,
    )


def score_sentiment(text: str) -> float:
    """
    Returns a continuous sentiment score in [-1.0, 1.0], computed as the
    probability-weighted average across negative/neutral/positive classes
    rather than just the top label, so "mostly negative but not certain"
    scores differently from "overwhelmingly negative".
    """
    if not text.strip():
        return 0.0

    clf = _load_pipeline()
    results = clf(text, truncation=True, max_length=256)[0]

    weighted_sum = 0.0
    for entry in results:
        label = entry["label"].lower()
        weighted_sum += _LABEL_TO_SCORE.get(label, 0.0) * entry["score"]

    return round(weighted_sum, 4)
