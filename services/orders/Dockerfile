# Build context: repository root  (docker compose sets it)
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PREFECT_HOME=/tmp/prefect
WORKDIR /srv
COPY services/orders/requirements.txt requirements.txt
RUN pip install --require-hashes -r requirements.txt
COPY libs/common /tmp/common
RUN pip install --no-deps /tmp/common && rm -rf /tmp/common
COPY services/orders/app app
RUN useradd --system --uid 10001 app
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
