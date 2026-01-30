FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Expose the port FastAPI runs on
EXPOSE 8000

# The "--forwarded-allow-ips '*'" is the "magic" that fixes the Invalid HTTP error
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--forwarded-allow-ips", "*"]
