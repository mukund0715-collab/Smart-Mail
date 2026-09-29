"""
Phase 5: Multi-label intent classification via DistilBERT, compiled to
ONNX with INT8 dynamic quantization for low-latency CPU inference inside
the cpu_heavy_queue worker pool.

One-time setup (run offline, not per-request):

    from optimum.onnxruntime import ORTModelForSequenceClassification
    from optimum.onnxruntime.configuration import AutoQuantizationConfig
    from optimum.onnxruntime import ORTQuantizer

    model = ORTModelForSequenceClassification.from_pretrained(
        "distilbert-base-uncased", export=True
    )
    model.save_pretrained("ml_artifacts/intent_onnx_fp32")

    quantizer = ORTQuantizer.from_pretrained("ml_artifacts/intent_onnx_fp32")
    qconfig = AutoQuantizationConfig.avx512_vnni(is_static=False, per_channel=False)
    quantizer.quantize(save_dir=settings.INTENT_ONNX_DIR, quantization_config=qconfig)

The fine-tuned classification head (mapping logits -> the label set in
core.models.EmailMetadata.IntentCategory) is assumed to already exist in
the exported checkpoint; swap in your fine-tuned model id before export.
"""
import logging
from functools import lru_cache
from typing import List, Tuple

from django.conf import settings

logger = logging.getLogger(__name__)

INTENT_LABELS: List[str] = [
    "billing",
    "payment_failure",
    "technical_support",
    "account_access",
    "feature_request",
    "general_inquiry",
    "spam",
    "other",
]


@lru_cache(maxsize=1)
def _load_pipeline():
    """
    Loaded once per worker process (memoized) — this is the expensive
    step (~tens of ms) we never want to repeat per-task under prefork.
    """
    from optimum.onnxruntime import ORTModelForSequenceClassification
    from transformers import AutoTokenizer, pipeline

    model = ORTModelForSequenceClassification.from_pretrained(
        settings.INTENT_ONNX_DIR,
        provider="CPUExecutionProvider",
        # O3/O4 graph-level optimizations applied at export/quantize time;
        # the saved graph already reflects them, so default session here.
    )
    tokenizer = AutoTokenizer.from_pretrained(settings.INTENT_MODEL_NAME)

    return pipeline(
        "text-classification",
        model=model,
        tokenizer=tokenizer,
        top_k=None,  # return scores for every label, not just the argmax
        function_to_apply="sigmoid",  # multi-label, not softmax
    )


def classify_intent(text: str) -> Tuple[str, float]:
    """
    Returns (top_label, confidence) for the highest-scoring intent label.
    """
    if not text.strip():
        return "other", 0.0

    clf = _load_pipeline()
    results = clf(text, truncation=True, max_length=256)[0]
    top = max(results, key=lambda r: r["score"])
    return top["label"], float(top["score"])
