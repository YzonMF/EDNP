"""Create the configured MySQL database if it doesn't exist yet (run before migrate)."""
import os
import sys
import time

import django
import MySQLdb

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ednp.settings')
django.setup()

from django.conf import settings  # noqa: E402

db = settings.DATABASES['default']
name = db['NAME']
if '`' in name:
    sys.exit(f'Refusing to create database with backtick in name: {name!r}')

for attempt in range(1, 11):
    try:
        conn = MySQLdb.connect(host=db['HOST'], port=int(db['PORT']), user=db['USER'], passwd=db['PASSWORD'])
        break
    except MySQLdb.OperationalError as exc:
        print(f'ensure_db: waiting for MySQL ({attempt}/10): {exc}', flush=True)
        time.sleep(3)
else:
    sys.exit('ensure_db: could not reach MySQL')

conn.cursor().execute(f'CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4')
conn.close()
print(f'ensure_db: database {name!r} is ready', flush=True)
