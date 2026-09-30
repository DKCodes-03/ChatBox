# Use a small Python image for the API container.
FROM python:3.12-slim

# Avoid creating .pyc files and make Python logs appear immediately in Docker.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies before copying the source so Docker can reuse this
# layer when application code changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the backend application and ingestion command into the image.
COPY app ./app
COPY ingestion ./ingestion

# The FastAPI entrypoint will be added in a later implementation task.
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
