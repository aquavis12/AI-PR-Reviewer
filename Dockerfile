# Lambda Orchestrator — Self-hosted container option
# This is the ORCHESTRATOR (receives webhooks, calls MicroVM API, posts comments).
# NOT the MicroVM image — see microvm-image/Dockerfile for that.

FROM python:3.11-slim

WORKDIR /app

# Install orchestrator dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ src/
COPY providers/ providers/
COPY lambda_handler.py .
COPY config.example.yaml config.yaml

EXPOSE 8080

CMD ["python", "-c", "from lambda_handler import handler; print('Use as Lambda or wrap with a web server')"]
