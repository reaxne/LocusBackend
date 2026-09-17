FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py database.py profile_schema.py settings.py ./
COPY migrations ./migrations
COPY recommendation ./recommendation

# Expand Railway's PORT at runtime and forward shutdown signals to Uvicorn.
CMD ["sh", "-c", "exec python -m uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
