# MicroVM Image for AI PR Reviewer
# This is the pre-baked image that runs INSIDE the Lambda MicroVM.
# It contains ALL security tools pre-installed so scans start instantly.
#
# Build once → create MicroVM image → every PR scan boots from this snapshot in ms.
#
# Supports: Python, Node.js, Java, Go, Terraform, CloudFormation scanning

FROM python:3.11-slim

# System dependencies + language runtimes
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    wget \
    jq \
    unzip \
    ca-certificates \
    openssh-client \
    # Node.js (for npm audit, eslint)
    nodejs \
    npm \
    # Java (for OWASP dependency-check, SpotBugs)
    default-jdk-headless \
    maven \
    # Ruby (for cfn-nag)
    ruby \
    ruby-dev \
    # Build essentials (some tools need compilation)
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# AWS CLI v2
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" \
    && unzip awscliv2.zip \
    && ./aws/install \
    && rm -rf aws awscliv2.zip

# Python security & quality tools
RUN pip install --no-cache-dir \
    bandit \
    pip-audit \
    ruff \
    safety \
    pipdeptree \
    semgrep \
    checkov \
    detect-secrets \
    trufflehog

# Node.js security tools
RUN npm install -g \
    eslint \
    @eslint/js \
    eslint-plugin-security \
    retire

# Terraform tools
RUN curl -sL https://github.com/aquasecurity/tfsec/releases/latest/download/tfsec-linux-amd64 -o /usr/local/bin/tfsec \
    && chmod +x /usr/local/bin/tfsec

# Infracost (Terraform cost estimation)
RUN curl -sL https://github.com/infracost/infracost/releases/latest/download/infracost-linux-amd64.tar.gz | tar xz -C /tmp \
    && mv /tmp/infracost-linux-amd64 /usr/local/bin/infracost \
    && chmod +x /usr/local/bin/infracost

# CloudFormation tools
RUN pip install --no-cache-dir cfn-lint \
    && gem install cfn-nag --no-document

# Gitleaks (secrets detection)
RUN curl -sL https://github.com/gitleaks/gitleaks/releases/latest/download/gitleaks_8.18.0_linux_x64.tar.gz | tar xz -C /tmp \
    && mv /tmp/gitleaks /usr/local/bin/gitleaks \
    && chmod +x /usr/local/bin/gitleaks

# OWASP Dependency-Check
RUN mkdir -p /opt/dependency-check \
    && curl -sL https://github.com/jeremylong/DependencyCheck/releases/download/v9.0.9/dependency-check-9.0.9-release.zip -o /tmp/dc.zip \
    && unzip /tmp/dc.zip -d /opt/dependency-check \
    && rm /tmp/dc.zip \
    && ln -s /opt/dependency-check/dependency-check/bin/dependency-check.sh /usr/local/bin/dependency-check

# Create workspace for cloning repos
RUN mkdir -p /workspace /results

WORKDIR /app

# Copy the executor HTTP server (receives commands from orchestrator)
COPY server.py .

EXPOSE 8080

CMD ["python", "server.py"]
