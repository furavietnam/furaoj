import os

import gevent.monkey

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dmoj.settings')
gevent.monkey.patch_all()

import django
django.setup()

from judge.bridge.daemon import judge_daemon

if __name__ == '__main__':
    judge_daemon()
