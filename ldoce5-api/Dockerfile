FROM python:3.11-slim-bookworm

# Build deps for python-lzo (LZO compression in MDict files)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libc-dev \
        liblzo2-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

ENV API_HOST=0.0.0.0
ENV API_PORT=5050
EXPOSE 5050

CMD ["sh", "-c", "uvicorn main:app --host $API_HOST --port $API_PORT"]
