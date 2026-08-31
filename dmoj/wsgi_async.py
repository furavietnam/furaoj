import os

import gevent.monkey

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dmoj.settings')
gevent.monkey.patch_all()

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
