"""
Celery application instance.

This module is imported by both the Django web process (to call .delay()) and
the Celery worker process.  It must not import any heavy dependencies at module
level — PySpark in particular must only be imported inside task bodies, because
PySpark initialises a JVM on import and would crash the web process.
"""
import os

from celery import Celery

# Point Celery at Django settings so it can read CELERY_BROKER_URL etc.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("rhombus")

# namespace="CELERY" means all config keys in settings.py must be prefixed
# with CELERY_ (e.g. CELERY_BROKER_URL).
app.config_from_object("django.conf:settings", namespace="CELERY")

# autodiscover_tasks() would look for tasks/tasks.py — our module is
# tasks/pipeline.py, so we rely on CELERY_IMPORTS in settings.py instead.
# We still call autodiscover_tasks() with no args to pick up any future
# tasks.py files added inside Django apps.
app.autodiscover_tasks()
