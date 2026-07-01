FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY src/ src/
COPY providers/ providers/
COPY kiro/ kiro/
COPY lambda_handler.py .
COPY config.example.yaml config.yaml

# Expose port for local testing
EXPOSE 8080

# For Lambda deployment, use lambda_handler.handler
# For local/container deployment, use the entrypoint below
CMD ["python", "-m", "uvicorn", "lambda_handler:handler", "--host", "0.0.0.0", "--port", "8080"]
