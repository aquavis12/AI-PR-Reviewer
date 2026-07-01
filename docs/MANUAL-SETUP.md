# Manual Setup Guide — AI PR Reviewer

Step-by-step setup on AWS. No CI/CD needed — all done via CLI.

---

## Prerequisites

- AWS CLI v2 configured (`aws configure`)
- Docker installed (for building MicroVM image)
- GitHub PAT with `repo` + `pull_requests` permissions
- AWS Account with Bedrock access (Kimi K2.5 enabled in us-east-1)

---

## Step 1: Create IAM Role for Lambda

```bash
# Create trust policy
cat > trust-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"Service": "lambda.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

# Create role
aws iam create-role \
  --role-name ai-pr-reviewer-role \
  --assume-role-policy-document file://trust-policy.json

# Attach policies
aws iam attach-role-policy --role-name ai-pr-reviewer-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

aws iam attach-role-policy --role-name ai-pr-reviewer-role \
  --policy-arn arn:aws:iam::aws:policy/AmazonBedrockFullAccess
```

Create inline policy for Secrets Manager + MicroVMs:

```bash
cat > inline-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:us-east-1:*:secret:github-pat-pr-reviewer*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "lambda-microvms:*"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject"
      ],
      "Resource": "arn:aws:s3:::ai-pr-reviewer-*/*"
    }
  ]
}
EOF

aws iam put-role-policy --role-name ai-pr-reviewer-role \
  --policy-name ai-pr-reviewer-permissions \
  --policy-document file://inline-policy.json
```

---

## Step 2: Store GitHub Token in Secrets Manager

```bash
aws secretsmanager create-secret \
  --name github-pat-pr-reviewer \
  --secret-string '{"token":"ghp_YOUR_GITHUB_PAT_HERE"}' \
  --region us-east-1
```

Note the ARN from the output — you'll need it in Step 5.

---

## Step 3: Build & Create MicroVM Image

```bash
cd microvm-image

# Build Docker image
docker build -t pr-review-scanner .

# Save as tar, then zip for upload
docker save pr-review-scanner | gzip > microvm-image.tar.gz

# Create S3 bucket for artifacts (if not exists)
aws s3 mb s3://ai-pr-reviewer-artifacts-$(aws sts get-caller-identity --query Account --output text) --region us-east-1

# Upload to S3
aws s3 cp microvm-image.tar.gz s3://ai-pr-reviewer-artifacts-$(aws sts get-caller-identity --query Account --output text)/microvm-image.tar.gz

# Create MicroVM image
aws lambda-microvms create-microvm-image \
  --name pr-review-scanner \
  --code-artifact uri=s3://ai-pr-reviewer-artifacts-$(aws sts get-caller-identity --query Account --output text)/microvm-image.tar.gz \
  --base-image-arn arn:aws:lambda:us-east-1:aws:microvm-image:al2023-1 \
  --region us-east-1
```

Wait for image status to be `Created`:

```bash
aws lambda-microvms get-microvm-image \
  --name pr-review-scanner \
  --region us-east-1
```

---

## Step 4: Create Lambda Layer

```bash
cd ..  # back to repo root

# Create layer directory
mkdir -p layer/python/lib/python3.11/site-packages

# Install dependencies
pip install requests boto3 pyyaml -t layer/python/lib/python3.11/site-packages --quiet

# Zip
cd layer && zip -r ../layer.zip python/ && cd ..

# Publish layer
aws lambda publish-layer-version \
  --layer-name ai-pr-reviewer-deps \
  --zip-file fileb://layer.zip \
  --compatible-runtimes python3.11 \
  --region us-east-1
```

Note the `LayerVersionArn` from the output.

---

## Step 5: Deploy Lambda Function

```bash
# Package orchestrator code
zip -r lambda.zip lambda_handler.py src/ providers/

# Create function
aws lambda create-function \
  --function-name ai-pr-reviewer \
  --runtime python3.11 \
  --handler lambda_handler.handler \
  --zip-file fileb://lambda.zip \
  --role arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):role/ai-pr-reviewer-role \
  --timeout 300 \
  --memory-size 512 \
  --environment "Variables={GITHUB_TOKEN_SECRET_ARN=arn:aws:secretsmanager:us-east-1:$(aws sts get-caller-identity --query Account --output text):secret:github-pat-pr-reviewer-XXXXXX,AWS_REGION=us-east-1}" \
  --layers <LAYER_VERSION_ARN_FROM_STEP_4> \
  --region us-east-1
```

Replace:
- `<LAYER_VERSION_ARN_FROM_STEP_4>` with the actual LayerVersionArn
- `XXXXXX` with the random suffix from your secret ARN (check in console or `aws secretsmanager list-secrets`)

---

## Step 6: Create Function URL

```bash
# Create public Function URL
aws lambda create-function-url-config \
  --function-name ai-pr-reviewer \
  --auth-type NONE \
  --region us-east-1

# Add public invoke permission
aws lambda add-permission \
  --function-name ai-pr-reviewer \
  --statement-id public-invoke \
  --action lambda:InvokeFunctionUrl \
  --principal "*" \
  --function-url-auth-type NONE \
  --region us-east-1
```

Note your Function URL from the output (e.g., `https://abc123.lambda-url.us-east-1.on.aws/`).

---

## Step 7: Test

```bash
# Health check
curl https://YOUR_FUNCTION_URL/health

# Test a PR review
curl -X POST https://YOUR_FUNCTION_URL/ \
  -H 'Content-Type: application/json' \
  -d '{
    "provider": "github",
    "owner": "aquavis12",
    "repo": "AI-PR-Reviewer",
    "pr_number": 1
  }'
```

---

## Step 8: Connect to GitHub

### Option A: GitHub Actions (per-repo)

Copy `.github/workflows/ai-review.yml` into any repo and set the secret:

```
Repository → Settings → Secrets → Actions → New secret
Name: AI_REVIEWER_API_URL
Value: https://YOUR_FUNCTION_URL/
```

### Option B: GitHub Webhook (org-wide)

```
Repository (or Org) → Settings → Webhooks → Add webhook
Payload URL: https://YOUR_FUNCTION_URL/
Content type: application/json
Events: Pull requests (opened, synchronize, reopened)
```

---

## Updating Code

When you change the orchestrator code:

```bash
# Re-zip and deploy
zip -r lambda.zip lambda_handler.py src/ providers/
aws lambda update-function-code \
  --function-name ai-pr-reviewer \
  --zip-file fileb://lambda.zip \
  --region us-east-1
```

---

## Updating MicroVM Image

When you add new tools to `microvm-image/Dockerfile`:

```bash
cd microvm-image
docker build -t pr-review-scanner .
docker save pr-review-scanner | gzip > microvm-image.tar.gz
aws s3 cp microvm-image.tar.gz s3://ARTIFACTS_BUCKET/microvm-image.tar.gz

# Delete old image and recreate
aws lambda-microvms delete-microvm-image \
  --name pr-review-scanner \
  --region us-east-1

aws lambda-microvms create-microvm-image \
  --name pr-review-scanner \
  --code-artifact uri=s3://ARTIFACTS_BUCKET/microvm-image.tar.gz \
  --base-image-arn arn:aws:lambda:us-east-1:aws:microvm-image:al2023-1 \
  --region us-east-1
```

---

## Updating Lambda Layer

When you add new Python dependencies:

```bash
rm -rf layer/
mkdir -p layer/python/lib/python3.11/site-packages
pip install requests boto3 pyyaml -t layer/python/lib/python3.11/site-packages --quiet
cd layer && zip -r ../layer.zip python/ && cd ..

LAYER_ARN=$(aws lambda publish-layer-version \
  --layer-name ai-pr-reviewer-deps \
  --zip-file fileb://layer.zip \
  --compatible-runtimes python3.11 \
  --query 'LayerVersionArn' --output text \
  --region us-east-1)

aws lambda update-function-configuration \
  --function-name ai-pr-reviewer \
  --layers $LAYER_ARN \
  --region us-east-1
```

---

## For GitLab

Same setup, but also store GitLab token:

```bash
aws secretsmanager create-secret \
  --name gitlab-token-pr-reviewer \
  --secret-string '{"token":"glpat-YOUR_GITLAB_TOKEN"}' \
  --region us-east-1
```

Add `GITLAB_TOKEN_SECRET_ARN` to Lambda env vars:

```bash
aws lambda update-function-configuration \
  --function-name ai-pr-reviewer \
  --environment "Variables={GITHUB_TOKEN_SECRET_ARN=...,GITLAB_TOKEN_SECRET_ARN=...,AWS_REGION=us-east-1}" \
  --region us-east-1
```

---

## Costs

| Resource | Cost |
|----------|------|
| Lambda (orchestrator) | ~$0.001/invocation |
| Lambda MicroVM | ~$0.005/scan (billed per-ms) |
| Bedrock Kimi K2.5 | ~$0.003/review |
| Secrets Manager | $0.40/secret/month |
| S3 (artifacts) | < $0.01/month |
| **Total per PR review** | **< $0.01** |

---

## Checklist

- [ ] IAM role created with Bedrock + SecretsManager + MicroVMs permissions
- [ ] GitHub PAT stored in Secrets Manager
- [ ] MicroVM image built and created (status: `Created`)
- [ ] Lambda Layer published (requests + boto3 + pyyaml)
- [ ] Lambda function deployed with env vars
- [ ] Function URL created (auth type: NONE)
- [ ] Health check returns `{"status": "healthy"}`
- [ ] Test PR review posts comment successfully
- [ ] GitHub webhook or Actions workflow configured
