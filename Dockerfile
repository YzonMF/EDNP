FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1

# mysqlclient needs the MariaDB client headers + a compiler; WeasyPrint needs Pango.
RUN apt-get update && apt-get install -y --no-install-recommends         build-essential pkg-config default-libmysqlclient-dev         libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core     && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Settings won't load without a SECRET_KEY, or with real SMS and no API key, and Railway's
# variables don't reach build steps. These throwaway values apply to this one command; the
# running container gets the real variables, so SMS there is still real.
RUN SECRET_KEY=build-only SMS_BACKEND=test python manage.py collectstatic --noinput

RUN mkdir -p /data/media
ENV MEDIA_ROOT=/data/media

EXPOSE 8000

# Railway injects $PORT; fall back to 8000 for local runs. --timeout: a worker busy with one
# request longer than this is restarted, and the documents archive streams for as long as its
# download takes (gunicorn's default 30 seconds would cut a large one off).
CMD ["sh", "-c", "python ensure_db.py && python manage.py migrate --noinput && gunicorn ednp.wsgi --bind 0.0.0.0:${PORT:-8000} --workers 3 --timeout 600"]
