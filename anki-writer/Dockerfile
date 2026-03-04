FROM python:3.11-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY anki_writer.py .

ENV API_HOST=0.0.0.0
ENV API_PORT=5051
EXPOSE 5051

CMD ["sh", "-c", "uvicorn anki_writer:app --host $API_HOST --port $API_PORT"]
