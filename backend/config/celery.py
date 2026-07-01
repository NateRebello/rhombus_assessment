"""Celery app instance. PySpark must not be imported here — only inside task bodies."""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("rhombus")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
