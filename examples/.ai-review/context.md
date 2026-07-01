# Project Context

## What This Project Does

This is a payment processing microservice that handles credit card transactions
via Stripe and PayPal APIs. It processes ~10,000 transactions/day in production.

## Architecture

- FastAPI backend (Python 3.11)
- PostgreSQL database (RDS Aurora)
- Redis cache for rate limiting
- SQS queues for async processing
- Deployed on ECS Fargate

## Code Standards

- All functions must have type hints
- All public functions must have docstrings
- No bare except blocks — always specify exception type
- Logging must use structlog (not print or stdlib logging)
- Database queries must use SQLAlchemy ORM (no raw SQL)
- All API endpoints must validate input with Pydantic models

## Security Requirements (PCI-DSS)

- NEVER log full card numbers — only last 4 digits
- All PII must be encrypted at rest (AES-256)
- API endpoints must validate JWT tokens
- Rate limiting required on all public endpoints
- No secrets in code — use AWS Secrets Manager
- All dependencies must be pinned to exact versions

## Testing

- New features require unit tests (pytest)
- Integration tests required for payment flows
- Coverage must stay above 85%
