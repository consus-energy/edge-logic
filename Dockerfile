# Use slim Python base
FROM python:3.11-slim

# Prevent Python from writing .pyc files and buffer logs
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# Install system deps if needed (add more if your packages require it)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl && rm -rf /var/lib/apt/lists/*

# Create a non-root user
RUN adduser --disabled-password --gecos "" app
WORKDIR /app

# Copy requirements first (to leverage build cache)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your repo (main.py, core/, utils/, bootstrap/, etc.)
COPY . .

USER app

# Run your edge main
CMD ["python", "main.py"]
