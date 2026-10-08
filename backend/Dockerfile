FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.lock /app/requirements.lock
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.lock && useradd --create-home --uid 10001 ift
COPY backend /app/backend
COPY shared /app/shared
RUN mkdir -p /data/uploads && chown -R ift:ift /data
USER ift
WORKDIR /app/backend
CMD ["sh", "-c", "alembic upgrade head && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log"]
