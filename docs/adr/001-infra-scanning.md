# ADR-001: Infrastructure Scanning — Terraform, CloudFormation & Cost Estimation

**Status:** Accepted  
**Date:** 2026-07-01  
**Branch:** `feature/infra-scanning`

---

## Context

The AI PR Reviewer currently scans application code (Python, JS, Java) for security vulnerabilities and quality issues. However, many PRs contain infrastructure-as-code (IaC) changes — Terraform `.tf` files, CloudFormation templates (`.yaml`, `.json`), and CDK constructs.

These IaC changes can introduce:
- Security misconfigurations (public S3 buckets, open security groups, unencrypted resources)
- Compliance violations (missing tagging, no logging enabled)
- Cost surprises (oversized instances, missing auto-scaling, expensive service configurations)

We need to extend the scanner to cover IaC with the same review quality as application code.

---

## Decision

Add three new scan capabilities as a feature branch:

### 1. Terraform Scanning

**Tools:**
- `tfsec` — Static analysis for Terraform (misconfigs, CIS benchmarks)
- `checkov` — Policy-as-code scanner (Terraform, CloudFormation, Kubernetes)
- `terraform validate` — Syntax and provider validation

**What it catches:**
- Public access misconfigurations
- Missing encryption (at-rest, in-transit)
- Overly permissive IAM policies
- Missing logging/monitoring
- CIS AWS Foundations Benchmark violations

### 2. CloudFormation Scanning

**Tools:**
- `cfn-lint` — AWS CloudFormation linter
- `cfn-nag` — CloudFormation static analysis (security)
- `checkov` — Also supports CFN templates

**What it catches:**
- Invalid resource properties
- Security group misconfigurations
- Missing DeletionPolicy on stateful resources
- Hardcoded secrets in templates
- Non-compliant resource configurations

### 3. Cost Estimation (Terracost / Infracost)

**Tools:**
- `infracost` — Cloud cost estimation from Terraform plans
- Terracost API — Cost diff between base and PR branch

**What it provides:**
- Monthly cost estimate for new/changed resources
- Cost diff (before vs after PR)
- Per-resource breakdown
- Cost threshold alerts (e.g., flag if PR adds > $50/month)

---

## Architecture

```
PR with IaC changes
       │
       ▼
┌─────────────────────┐
│ Diff Analyzer        │
│ • Detects .tf files  │
│ • Detects CFN yamls  │
│ • Routes to scanner  │
└──────────┬──────────┘
           │
     ┌─────┼─────────────┐
     ▼     ▼             ▼
┌────────┐ ┌──────────┐ ┌───────────┐
│ tfsec  │ │ cfn-lint │ │ infracost │
│ checkov│ │ cfn-nag  │ │ terracost │
└────┬───┘ └────┬─────┘ └─────┬─────┘
     │          │              │
     └──────────┼──────────────┘
                ▼
┌─────────────────────┐
│ AI Analysis          │
│ (Bedrock or Kiro)    │
│ + Context Files      │
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ PR Comment           │
│ • Security findings  │
│ • Cost impact        │
│ • Recommendations    │
└─────────────────────┘
```

---

## Context Files System

Both review approaches (Bedrock LLM and Kiro headless) support **context files that live in the user's own repo** under `.ai-review/`. When the MicroVM clones the repo, it reads these files and injects them into the AI review.

### Purpose
Context files tell the AI *what the code does* and *what standards to apply*. Users maintain them in their own repos — like `.github/`, `.eslintrc`, or `.editorconfig`.

What to include:
- Code quality rules specific to your org
- Architecture decisions and patterns
- Compliance requirements (SOC2, HIPAA, PCI-DSS)
- Cost budget constraints
- Terraform module documentation

### Structure (in user's repo)
```
their-repo/
├── .ai-review/
│   ├── context.md         # Main project context (what it does, architecture)
│   ├── security.md        # Security-specific rules
│   ├── cost-policy.md     # Cost thresholds and budget rules
│   └── terraform/
│       └── standards.md   # Terraform-specific rules
├── src/
└── ...
```

### How It Works

**Bedrock LLM approach:**
- MicroVM clones repo → reads `.ai-review/` → passes to Lambda
- Context injected as prompt preamble before scan results
- Loading order: `context.md` → category-specific → all other .md files
- AI uses this context to produce more relevant, org-specific findings

**Kiro headless approach:**
- Kiro reads `.ai-review/` as reference material during review
- Kiro reads them as reference material during review
- Enables Kiro to understand project intent and architecture decisions

### Example Context File
```markdown
## Cost Policy

- Monthly budget per environment: $500 (dev), $2000 (staging), $5000 (prod)
- Flag any single resource > $100/month
- Require approval for any RDS instance larger than db.t3.medium
- No NAT Gateways in dev environments (use VPC endpoints)
- Spot instances required for non-production batch workloads
```

---

## Detection Logic

Added to `diff_analyzer.py`:

```python
IAC_FILES = {
    r"\.tf$": "terraform",
    r"\.tfvars$": "terraform",
    r"template\.yaml$|template\.json$|cfn-.*\.yaml$": "cloudformation",
    r"\.yaml$|\.json$":  # Check content for AWSTemplateFormatVersion
}
```

When IaC files are detected in a PR:
1. `relevant_scans` includes `terraform` or `cloudformation`
2. Cost estimation runs if `.tf` files have resource changes
3. AI review includes IaC-specific context from context files

---

## PR Comment Format (IaC)

```markdown
## 🛡️ AI Code Review — Infrastructure

🟡 **Risk Level: MEDIUM**

> PR adds 3 new AWS resources. Found 2 security misconfigurations
> and estimated monthly cost increase of $47.50.

### Security Findings

| Tool | Severity | Finding |
|------|----------|---------|
| tfsec | HIGH | S3 bucket without encryption (aws_s3_bucket.data) |
| checkov | MEDIUM | Security group allows 0.0.0.0/0 ingress on port 22 |

### Cost Impact

| Resource | Type | Monthly Cost |
|----------|------|-------------|
| aws_rds_instance.main | db.t3.large | +$98.50 |
| aws_nat_gateway.main | NAT Gateway | +$32.40 |
| aws_s3_bucket.data | S3 Standard | +$0.23 |
| **Total** | | **+$131.13/month** |

⚠️ **Cost threshold exceeded** — PR adds > $100/month. Requires approval.

### Recommendations

1. **Enable S3 encryption** [critical] _(effort: trivial)_
2. **Restrict SSH to VPN CIDR** [high] _(effort: small)_
3. **Consider db.t3.medium for non-prod** [medium] _(effort: small)_ — saves $49/month
```

---

## Alternatives Considered

| Option | Pros | Cons |
|--------|------|------|
| tfsec only | Simple, fast | No CFN support, no cost |
| Checkov only | Multi-framework | Slower, no cost estimation |
| OPA/Rego policies | Highly customizable | Complex to author, steep learning curve |
| **tfsec + cfn-lint + infracost** | Best coverage, fast, cost-aware | Multiple tools to maintain |

---

## Consequences

### Positive
- PRs with IaC changes now get security review (previously unscanned)
- Cost visibility before merge (prevents surprise bills)
- Context files allow org-specific rules without code changes
- Same review experience whether using Bedrock or Kiro

### Negative
- Additional tools to install in the scan environment
- Infracost requires a free API key for cost data
- Terraform plan generation may need cloud credentials for full accuracy

### Risks
- Cost estimates are approximations (actual usage may differ)
- Tools may produce false positives on valid Terraform patterns
- Context files need maintenance as org standards evolve

---

## Implementation Plan

1. Add IaC detection to `diff_analyzer.py`
2. Create `src/iac_scanner.py` (tfsec, cfn-lint, checkov wrappers)
3. Create `src/cost_estimator.py` (infracost integration)
4. Create `src/context_manager.py` (upload, load, apply context files)
5. Add `/context` endpoints to `lambda_handler.py`
6. Update `kiro/review-spec.md` with IaC review rules
7. Update `ai_client.py` prompt to include IaC + cost sections
8. Add IaC-specific comment formatting
9. Tests + documentation

---

## References

- [tfsec](https://github.com/aquasecurity/tfsec)
- [checkov](https://github.com/bridgecrewio/checkov)
- [cfn-lint](https://github.com/aws-cloudformation/cfn-lint)
- [cfn-nag](https://github.com/stelligent/cfn_nag)
- [infracost](https://github.com/infracost/infracost)
- [Kiro Headless Docs](https://kiro.dev/docs/headless)
