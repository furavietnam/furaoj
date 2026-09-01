import logging
import os
import socket

from celery import Celery
from celery.schedules import crontab
from celery.signals import task_failure

app = Celery('dmoj')

from django.conf import settings  # noqa: E402, I202, django must be imported here

# Configure Celery explicitly to avoid Celery 6.0 deprecation warnings about
# CELERY_RESULT_BACKEND and CELERY_TIMEZONE.
app.conf.timezone = getattr(settings, 'TIMEZONE', 'UTC')
app.conf.broker_url = (
    getattr(settings, 'CELERY_BROKER_URL_SECRET', None)
    or getattr(settings, 'CELERY_BROKER_URL', None)
    or 'redis://localhost:6379//'
)
result_backend = (
    getattr(settings, 'CELERY_RESULT_BACKEND_SECRET', None)
    or getattr(settings, 'CELERY_RESULT_BACKEND', None)
)
if result_backend:
    app.conf.result_backend = result_backend

# Load task modules from all registered Django app configs.
app.autodiscover_tasks()

# Logger to enable reporting of errors.
logger = logging.getLogger('judge.celery')

# Load periodic tasks
app.conf.beat_schedule = {
    'daily-queue-time-stats': {
        'task': 'judge.tasks.webhook.queue_time_stats',
        'schedule': crontab(minute=0, hour=0),
        'options': {
            'expires': 60 * 60 * 24,
        },
    },
    'problem-garbage-collector': {
        'task': 'judge.tasks.problem.problem_garbage_collect',
        'schedule': crontab(**settings.FURAOJ_PROBLEM_GARBAGE_COLLECTOR_CRONTAB_KWARGS),
        'options': {
            'expires': 60 * 60 * 24,
        },
    },
    'organization-monthly-reset': {
        'task': 'judge.tasks.organization.organization_monthly_reset',
        'schedule': crontab(minute=0, hour=0, day_of_month=1),
        'options': {
            'expires': 60 * 60 * 24,
        },
    },
}


@task_failure.connect()
def celery_failure_log(sender, task_id, exception, traceback, *args, **kwargs):
    logger.error('Celery Task %s: %s on %s', sender.name, task_id, socket.gethostname(),  # noqa: G201
                 exc_info=(type(exception), exception, traceback))
