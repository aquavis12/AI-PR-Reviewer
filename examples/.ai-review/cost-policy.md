# Cost Policy

## Budget
- Monthly budget: $3,000 (production), $500 (staging), $200 (dev)
- Flag any PR that adds > $100/month
- No NAT Gateways in dev/staging
- RDS: max db.t3.medium for non-prod

## Optimization Rules
- Use Graviton (ARM) instances where supported
- Spot instances for batch workers
- S3 lifecycle: IA after 30 days, Glacier after 90 days
- Auto-scaling required for all ECS services
