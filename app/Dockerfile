FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /workspace

# Copy requirements file first for layer caching
COPY requirements.txt ./app/requirements.txt
RUN pip install --no-cache-dir -r ./app/requirements.txt

# Copy all application files to the /workspace/app directory
COPY . ./app/

EXPOSE 8000

# Run uvicorn referencing the app module path
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
