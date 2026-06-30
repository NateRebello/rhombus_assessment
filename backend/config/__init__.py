# config/__init__.py
# Import the Celery app here so it is initialised when Django starts.
# This ensures the app is available for .delay() calls in views without
# requiring an explicit `from config.celery import app` in each module.
from .celery import app as celery_app

__all__ = ["celery_app"]
