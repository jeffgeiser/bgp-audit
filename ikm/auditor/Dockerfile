FROM python:3.11-slim

WORKDIR /app

COPY auditor/requirements.txt /tmp/auditor-requirements.txt
RUN pip install --no-cache-dir -r /tmp/auditor-requirements.txt

COPY shared/ /app/shared/
COPY auditor/ /app/auditor/

WORKDIR /app/auditor
EXPOSE 8501

CMD ["streamlit", "run", "auditor.py", "--server.port", "8501", "--server.address", "0.0.0.0", "--server.headless", "true"]
