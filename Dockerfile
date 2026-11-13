# One image for the pipeline (one-shot job) and the API (long-running).
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY pipeline ./pipeline
COPY api ./api
COPY docs ./docs
COPY tests ./tests
CMD ["python", "-m", "pipeline.run"]
