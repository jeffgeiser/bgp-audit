FROM python:3.11-slim

WORKDIR /app

# Install system deps for sentence-transformers
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && \
    rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Create directory for persistent data
RUN mkdir -p /app/data

# Set environment variables
ENV PEERINGDB_DB_PATH=/app/data/peeringdb.sqlite3

# Expose the port FastAPI runs on
EXPOSE 8000

# The "--forwarded-allow-ips '*'" is the "magic" that fixes the Invalid HTTP error
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--forwarded-allow-ips", "*"]
