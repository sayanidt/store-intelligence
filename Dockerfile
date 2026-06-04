FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /workspace

# Copy requirements file first for layer caching
COPY app/requirements.txt ./app/requirements.txt
RUN pip install --no-cache-dir -r ./app/requirements.txt

# Copy all application files
COPY . .

# Expose port 8000
EXPOSE 8000

# Set environment variables for the database and files in root copy
ENV DATABASE_PATH=/workspace/data/store_intelligence.db
ENV POS_CSV_PATH=/workspace/data/pos_transactions.csv
ENV EVENTS_JSONL_PATH=/workspace/data/sample_events.jsonl

# Run uvicorn referencing the app module path
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
