FROM python:3.11-slim

WORKDIR /app

COPY chat/requirements.txt /tmp/chat-requirements.txt
RUN pip install --no-cache-dir -r /tmp/chat-requirements.txt

COPY shared/ /app/shared/
COPY chat/ /app/chat/

WORKDIR /app/chat
EXPOSE 8000

CMD ["chainlit", "run", "main.py", "--host", "0.0.0.0", "--port", "8000"]
