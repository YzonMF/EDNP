FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1

# mysqlclient needs the MariaDB client headers + a compiler; WeasyPrint needs Pango.
RUN apt-get update && apt-get install -y --no-install-recommends         build-essential pkg-config default-libmysqlclient-dev         libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core     && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SECRET_KEY is required at import time; this throwaway value is only for the build step.
RUN SECRET_KEY=build-only python manage.py collectstatic --noinput

RUN mkdir -p /data/media
ENV MEDIA_ROOT=/data/media

EXPOSE 8000

# Railway injects $PORT; fall back to 8000 for local runs.
CMD ["sh", "-c", "python ensure_db.py && python manage.py migrate --noinput && gunicorn ednp.wsgi --bind 0.0.0.0:${PORT:-8000} --workers 3"]
