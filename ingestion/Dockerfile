# One image for Prefect server, Prefect runner, Dask scheduler and Dask workers (identical library versions).
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/srv PREFECT_HOME=/data/prefect
WORKDIR /srv
COPY ingestion/requirements.txt requirements.txt
RUN pip install --require-hashes -r requirements.txt
COPY libs/common /tmp/common
RUN pip install --no-deps /tmp/common && rm -rf /tmp/common
COPY ingestion ingestion
RUN useradd --system --uid 10001 --create-home app && mkdir -p /data/prefect && chown -R app /data
USER app
CMD ["python", "-m", "ingestion.serve"]
