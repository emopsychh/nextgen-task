# Celery app is imported from config.celery when the worker starts.
# Avoid importing celery at Django startup so manage.py works without broker deps in some envs.

__all__: list[str] = []
