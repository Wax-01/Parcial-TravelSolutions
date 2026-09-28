FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv
COPY db/requirements.txt requirements.txt
RUN pip install --require-hashes -r requirements.txt
COPY db/migrate.py db/migrations ./
RUN mkdir migrations && mv *.sql migrations/
RUN useradd --system --uid 10001 app
USER app
CMD ["python", "migrate.py"]
