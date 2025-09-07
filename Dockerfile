# ---- Build stage ----
FROM python:3.11-slim AS builder
WORKDIR /app
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
COPY requirements.txt .
RUN pip wheel --wheel-dir=/wheels -r requirements.txt

# ---- Runtime stage ----
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    VERSION=0.1.0 \
    PORT=8000
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt
COPY app ./app
COPY gunicorn_conf.py ./gunicorn_conf.py
EXPOSE 8000
# gunicorn + uvicorn 多 worker 更稳（生产推荐）
CMD ["gunicorn", "-c", "gunicorn_conf.py", "app.main:app"]

