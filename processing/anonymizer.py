"""
Phase 4: Privacy-first data masking layer.

Engines are instantiated once at *module import time* — not inside the
task function — so that under Celery's prefork worker pool, the spaCy
model and Presidio recognizers are loaded a single time per child process
(at fork) rather than being reloaded on every single task invocation.
"""
import logging
import re
from collections import defaultdict
from typing import Dict, Tuple

from django.conf import settings
from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig
from presidio_anonymizer.operators import Operator, OperatorType

logger = logging.getLogger(__name__)


class StatefulReversibleAnonymizer(Operator):
    """
    Custom Presidio operator that replaces each detected entity with a
    sequentially indexed synthetic token (<PERSON_1>, <PERSON_2>,
    <PHONE_1>, ...), guaranteeing the *same* underlying value always maps
    to the *same* token within a single anonymization pass, and records
    the reverse mapping for later restoration.

    Instantiate one fresh instance per email (it holds mutable state via
    `self.mapping` / `self.counters`) — do NOT share an instance across
    requests, and do NOT make this one a module-level singleton.
    """

    def __init__(self):
        self.mapping: Dict[str, str] = {}          # token -> original value
        self._value_to_token: Dict[Tuple[str, str], str] = {}  # (entity_type, value) -> token
        self.counters: Dict[str, int] = defaultdict(int)

    def operate(self, text: str, params: dict = None) -> str:
        entity_type = params["entity_type"]
        key = (entity_type, text)

        if key in self._value_to_token:
            return self._value_to_token[key]

        self.counters[entity_type] += 1
        token = f"<{entity_type}_{self.counters[entity_type]}>"

        self._value_to_token[key] = token
        self.mapping[token] = text
        return token

    def validate(self, params: dict = None) -> None:
        return None

    def operator_name(self) -> str:
        return "stateful_reversible_anonymizer"

    def operator_type(self) -> OperatorType:
        return OperatorType.Anonymize


# --------------------------------------------------------------------------
# Domain-specific custom recognizers, extending Presidio's built-ins
# (PERSON, EMAIL_ADDRESS, PHONE_NUMBER, CREDIT_CARD, etc.)
# --------------------------------------------------------------------------
_ORDER_ID_PATTERN = Pattern(name="order_id", regex=r"\b(ORD|INV)-\d{6,10}\b", score=0.85)
order_id_recognizer = PatternRecognizer(
    supported_entity="ORDER_ID", patterns=[_ORDER_ID_PATTERN]
)

_ACCOUNT_ID_PATTERN = Pattern(name="account_id", regex=r"\bACC-[A-Z0-9]{8}\b", score=0.85)
account_id_recognizer = PatternRecognizer(
    supported_entity="ACCOUNT_ID", patterns=[_ACCOUNT_ID_PATTERN]
)


def _build_analyzer_engine() -> AnalyzerEngine:
    nlp_configuration = {
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "en", "model_name": settings.SPACY_MODEL_NAME}],
    }
    nlp_engine = NlpEngineProvider(nlp_configuration=nlp_configuration).create_engine()

    engine = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])
    engine.registry.add_recognizer(order_id_recognizer)
    engine.registry.add_recognizer(account_id_recognizer)
    return engine


# Loaded once per worker process at import/fork time.
ANALYZER_ENGINE = _build_analyzer_engine()
ANONYMIZER_ENGINE = AnonymizerEngine()
ANONYMIZER_ENGINE.add_anonymizer(StatefulReversibleAnonymizer)

_ENTITIES_TO_MASK = [
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "IBAN_CODE",
    "US_SSN",
    "LOCATION",
    "ORDER_ID",
    "ACCOUNT_ID",
]


def anonymize_text(text: str) -> Tuple[str, Dict[str, str]]:
    """
    Run Presidio analysis + reversible anonymization over `text`.

    Returns:
        (masked_text, deanonymization_map) where deanonymization_map is
        {"<PERSON_1>": "Jane Doe", ...} suitable for JSONField storage
        and later string-replacement restoration.
    """
    if not text:
        return "", {}

    results = ANALYZER_ENGINE.analyze(text=text, entities=_ENTITIES_TO_MASK, language="en")

    operator = StatefulReversibleAnonymizer()
    anonymized_result = ANONYMIZER_ENGINE.anonymize(
        text=text,
        analyzer_results=results,
        operators={
            "DEFAULT": OperatorConfig(
                operator_name="stateful_reversible_anonymizer", params={}
            )
        },
        # Presidio instantiates operators from the registry by name; to
        # reuse *this* stateful instance (so tokens stay consistent across
        # every entity in the same email) we register and invoke it
        # directly rather than letting the engine construct a fresh one.
    )

    # NOTE: Presidio's public API constructs operator instances from the
    # registered class per call, which would reset state between entities.
    # To guarantee single-instance state across the whole document, do the
    # replacement pass manually, in reverse-offset order so earlier
    # replacements don't shift later offsets.
    masked_text = text
    for result in sorted(results, key=lambda r: r.start, reverse=True):
        original_value = text[result.start:result.end]
        token = operator.operate(original_value, {"entity_type": result.entity_type})
        masked_text = masked_text[: result.start] + token + masked_text[result.end :]

    return masked_text, operator.mapping


def restore_original_values(text: str, deanonymization_map: Dict[str, str]) -> str:
    """
    Reverse the masking: replace every synthetic token in `text` with its
    original value from the map. Used after the Gemini draft comes back
    (Phase 6) so the human-facing reply contains real names/contacts again.
    """
    restored = text
    # Replace longer tokens first to avoid partial-match collisions
    # (e.g. <PERSON_1> vs <PERSON_10>).
    for token in sorted(deanonymization_map, key=len, reverse=True):
        restored = restored.replace(token, deanonymization_map[token])
    return restored
