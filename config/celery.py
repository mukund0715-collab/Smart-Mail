"""
Celery application entrypoint.

Run three separate worker fleets so each gets the right concurrency model
(Phase 3 of the design doc):

    # I/O-bound ingestion writes — green threads, high concurrency
    celery -A config worker -Q io_fast_queue -P gevent -c 200 -n io_worker@%h

    # CPU-bound NLP (Presidio, DistilBERT, RoBERTa) — real processes,
    # one per physical core, so BLAS/PyTorch don't fight over the GIL.
    celery -A config worker -Q cpu_heavy_queue -P prefork -c 4 -n cpu_worker@%h

    # Outbound Gemini API calls — green threads, high concurrency, network-bound
    celery -A config worker -Q api_bound_queue -P gevent -c 100 -n api_worker@%h
"""
import os

from celery import Celery
from kombu import Queue

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("emailai")
app.config_from_object("django.conf:settings", namespace="CELERY")

app.conf.task_queues = (
    Queue("io_fast_queue"),
    Queue("cpu_heavy_queue"),
    Queue("api_bound_queue"),
)
app.conf.task_default_queue = "io_fast_queue"

app.autodiscover_tasks(["ingestion", "processing", "core", "frontend"], related_name="views")
app.autodiscover_tasks()

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
