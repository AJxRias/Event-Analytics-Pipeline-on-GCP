FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config.py ./
COPY pipeline ./pipeline
COPY api ./api
COPY scripts ./scripts
COPY sql ./sql

# Default: run the ETL job (Cloud Run Job). The mock API service overrides the command.
CMD ["python", "-m", "pipeline.main"]
