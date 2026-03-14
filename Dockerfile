FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY static/ ./static/
COPY templates/ ./templates/

RUN chmod +x src/auto_push.sh 2>/dev/null || true

CMD ["python", "src/main.py"]
