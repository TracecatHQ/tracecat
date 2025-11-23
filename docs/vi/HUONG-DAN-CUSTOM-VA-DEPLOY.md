# HƯỚNG DẪN CUSTOMIZATION VÀ DEPLOYMENT TRACECAT SOAR

## Mục lục
1. [Customization - Tùy chỉnh hệ thống](#1-customization-tùy-chỉnh-hệ-thống)
2. [Tạo Custom Integrations](#2-tạo-custom-integrations)
3. [Tạo Custom Actions](#3-tạo-custom-actions)
4. [Mở rộng Database Schema](#4-mở-rộng-database-schema)
5. [Customization Frontend](#5-customization-frontend)
6. [Packaging - Đóng gói hệ thống](#6-packaging-đóng-gói-hệ-thống)
7. [Deployment - Triển khai](#7-deployment-triển-khai)
8. [Monitoring và Maintenance](#8-monitoring-và-maintenance)

---

## 1. Customization - Tùy chỉnh hệ thống

### 1.1. Cấu trúc để Customization

Để customize Tracecat thành hệ thống SOAR riêng, bạn nên tạo structure như sau:

```
your-soar-system/
├── custom/                          # Custom code của bạn
│   ├── integrations/                # Custom integrations
│   │   ├── __init__.py
│   │   ├── your_siem.py            # Integration với SIEM của bạn
│   │   ├── your_edr.py             # Integration với EDR của bạn
│   │   └── internal_api.py         # Internal APIs
│   │
│   ├── templates/                   # Custom action templates
│   │   └── tools/
│   │       ├── your_siem/
│   │       │   ├── query_logs.yaml
│   │       │   └── create_alert.yaml
│   │       └── your_edr/
│   │           └── isolate_endpoint.yaml
│   │
│   ├── workflows/                   # Pre-built workflows
│   │   ├── phishing_investigation.yaml
│   │   ├── malware_analysis.yaml
│   │   └── incident_response.yaml
│   │
│   ├── schemas/                     # Custom database schemas
│   │   ├── models.py               # Additional database models
│   │   └── migrations/             # Custom migrations
│   │
│   ├── api/                         # Custom API endpoints
│   │   ├── routers/
│   │   │   ├── custom_router.py
│   │   │   └── reporting.py
│   │   └── dependencies.py
│   │
│   └── ui/                          # Custom UI components
│       ├── components/
│       └── pages/
│
├── config/                          # Configuration
│   ├── .env.production
│   ├── .env.staging
│   └── branding.json               # UI branding config
│
├── deployment/                      # Deployment configs
│   ├── docker/
│   │   ├── Dockerfile.custom
│   │   └── docker-compose.prod.yml
│   ├── kubernetes/
│   │   ├── base/
│   │   └── overlays/
│   └── scripts/
│       ├── backup.sh
│       └── restore.sh
│
├── docs/                            # Documentation
│   ├── SETUP.md
│   ├── WORKFLOWS.md
│   └── INTEGRATIONS.md
│
├── pyproject.toml                   # Your package definition
└── README.md
```

### 1.2. Setup Development Environment

#### 1. Clone Tracecat
```bash
git clone https://github.com/TracecatHQ/tracecat.git
cd tracecat
```

#### 2. Tạo custom branch
```bash
git checkout -b custom/your-company-soar
```

#### 3. Setup Python environment
```bash
# Create virtual environment với uv
uv venv

# Activate
source .venv/bin/activate  # Linux/Mac
# hoặc
.venv\Scripts\activate     # Windows

# Install dependencies
uv sync

# Install pre-commit hooks
uv run pre-commit install
```

#### 4. Setup Frontend
```bash
cd frontend
pnpm install
```

#### 5. Setup Infrastructure
```bash
# Copy environment template
cp .env.example .env

# Edit .env với configurations của bạn
vim .env

# Start development stack
just dev

# Hoặc manually
docker compose -f docker-compose.dev.yml up
```

### 1.3. Environment Configuration

**Tạo `.env` file**:

```bash
# =================
# CORE CONFIGURATION
# =================

TRACECAT__APP_ENV=development
# Options: development, staging, production

TRACECAT__PUBLIC_APP_URL=http://localhost
TRACECAT__PUBLIC_API_URL=http://localhost/api
NEXT_PUBLIC_APP_URL=http://localhost
NEXT_PUBLIC_API_URL=http://localhost/api

# =================
# SECURITY
# =================

# Generate: python -c "import secrets; print(secrets.token_urlsafe(32))"
TRACECAT__SERVICE_KEY=your-service-key-here
TRACECAT__SIGNING_SECRET=your-signing-secret-here
USER_AUTH_SECRET=your-auth-secret-here

# Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
TRACECAT__DB_ENCRYPTION_KEY=your-encryption-key-here

# =================
# DATABASE
# =================

TRACECAT__DB_URI=postgresql+psycopg://postgres:postgres@postgres_db:5432/postgres
TRACECAT__POSTGRES_USER=postgres
TRACECAT__POSTGRES_PASSWORD=postgres

# Connection pooling
TRACECAT__DB_POOL_SIZE=10
TRACECAT__DB_MAX_OVERFLOW=60

# =================
# TEMPORAL
# =================

TEMPORAL__CLUSTER_URL=temporal:7233
TEMPORAL__CLUSTER_QUEUE=tracecat-task-queue
TEMPORAL__CLUSTER_NAMESPACE=default
TEMPORAL__TASK_TIMEOUT=120

TEMPORAL__POSTGRES_USER=temporal
TEMPORAL__POSTGRES_PASSWORD=temporal

# =================
# STORAGE
# =================

# MinIO (S3-compatible)
MINIO_ROOT_USER=minio
MINIO_ROOT_PASSWORD=minio123
TRACECAT__BLOB_STORAGE_PROTOCOL=minio
TRACECAT__BLOB_STORAGE_ENDPOINT=http://minio:9000

# =================
# REDIS
# =================

REDIS_HOST=redis
REDIS_PORT=6379
REDIS_URL=redis://redis:6379

# =================
# AUTHENTICATION
# =================

TRACECAT__AUTH_TYPES=basic,google_oauth
# Options: basic, google_oauth, saml

# Password requirements
TRACECAT__AUTH_MIN_PASSWORD_LENGTH=8

# Domain whitelist (optional)
TRACECAT__AUTH_ALLOWED_DOMAINS=yourcompany.com,partner.com

# Google OAuth
OAUTH_CLIENT_ID=your-google-client-id
OAUTH_CLIENT_SECRET=your-google-client-secret

# SAML SSO
SAML_IDP_METADATA_URL=https://your-idp.com/metadata

# =================
# FEATURES
# =================

TRACECAT__FEATURE_FLAGS=agent_presets,case_durations,git_sync

# Available flags:
# - agent_presets: AI agent presets
# - case_durations: SLA tracking
# - git_sync: Git repository sync
# - agent_approvals: Approval workflows (EE)

# =================
# LOGGING
# =================

LOG_LEVEL=INFO
# Options: DEBUG, INFO, WARNING, ERROR, CRITICAL

# =================
# MONITORING (Optional)
# =================

# Sentry
SENTRY_DSN=https://your-sentry-dsn

# Langfuse (LLM observability)
LANGFUSE_HOST=https://cloud.langfuse.com
LANGFUSE_PUBLIC_KEY=pk-...
LANGFUSE_SECRET_KEY=sk-...

# =================
# CUSTOM CONFIGURATION
# =================

# Your custom environment variables
YOUR_COMPANY_API_URL=https://api.yourcompany.com
YOUR_COMPANY_API_KEY=your-api-key
```

---

## 2. Tạo Custom Integrations

### 2.1. Python Integration

**Example: Integration với SIEM nội bộ**

```python
# custom/integrations/your_siem.py

from typing import Any
from datetime import datetime
import httpx

from tracecat_registry import RegistrySecret, registry

@registry.register(
    namespace="custom.your_siem",
    description="Search logs in your SIEM",
    secrets=["your_siem"],  # Required secret name
)
async def search_logs(
    query: str,
    start_time: datetime,
    end_time: datetime,
    limit: int = 100,
    your_siem: RegistrySecret = None,  # Auto-injected
) -> dict[str, Any]:
    """
    Search logs in your SIEM.

    Args:
        query: Search query (Lucene syntax)
        start_time: Start time for search
        end_time: End time for search
        limit: Maximum number of results
        your_siem: SIEM credentials (auto-injected)

    Returns:
        {
            "total": int,
            "logs": list[dict],
            "query_id": str
        }
    """

    # Create HTTP client
    async with httpx.AsyncClient() as client:
        # Make API request
        response = await client.post(
            f"{your_siem['API_URL']}/api/v1/search",
            headers={
                "Authorization": f"Bearer {your_siem['API_KEY']}",
                "Content-Type": "application/json",
            },
            json={
                "query": query,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "limit": limit,
            },
            timeout=30.0,
        )
        response.raise_for_status()

        return response.json()


@registry.register(
    namespace="custom.your_siem",
    description="Create alert in your SIEM",
    secrets=["your_siem"],
)
async def create_alert(
    title: str,
    severity: str,  # low, medium, high, critical
    description: str,
    indicators: list[dict[str, Any]],
    your_siem: RegistrySecret = None,
) -> dict[str, Any]:
    """Create alert in your SIEM."""

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{your_siem['API_URL']}/api/v1/alerts",
            headers={
                "Authorization": f"Bearer {your_siem['API_KEY']}",
                "Content-Type": "application/json",
            },
            json={
                "title": title,
                "severity": severity,
                "description": description,
                "indicators": indicators,
            },
            timeout=30.0,
        )
        response.raise_for_status()

        return response.json()
```

**Register integration**:

```python
# custom/integrations/__init__.py

from tracecat_registry import registry
from . import your_siem

# Integration sẽ tự động được load khi import package

# Verify registration
print(f"Registered actions: {[a.name for a in registry.actions]}")
```

**Install integration**:

```python
# pyproject.toml

[project]
name = "your-soar-integrations"
version = "1.0.0"
dependencies = [
    "tracecat",
    "tracecat-registry",
    "httpx>=0.28.0",
]

[project.entry-points."tracecat.integrations"]
your_siem = "custom.integrations.your_siem"
```

```bash
# Install
uv pip install -e .

# Verify
python -c "from custom.integrations import your_siem"
```

### 2.2. YAML Action Template

**Example: Query logs template**

```yaml
# custom/templates/tools/your_siem/search_logs.yaml

type: action
definition:
  title: "Search SIEM logs"
  description: "Search logs in your company SIEM"
  display_group: "Your SIEM"
  namespace: "custom.your_siem"
  name: "search_logs"

  expects:
    query:
      type: str
      description: "Lucene search query"
    start_time:
      type: str
      description: "Start time (ISO format or relative like '-1h')"
    end_time:
      type: str | None
      description: "End time (ISO format, defaults to now)"
      default: null
    limit:
      type: int
      description: "Maximum results"
      default: 100

  returns:
    type: object
    description: "Search results"

  steps:
    - ref: "parse_time_range"
      action: "core.script.run_python"
      args:
        script: |
          from datetime import datetime, timedelta
          import dateparser

          def main(start_time, end_time):
              # Parse start time
              if start_time.startswith('-'):
                  # Relative time like '-1h', '-30m'
                  start = datetime.now() + timedelta(
                      hours=int(start_time[:-1]) if 'h' in start_time else 0,
                      minutes=int(start_time[:-1]) if 'm' in start_time else 0,
                  )
              else:
                  start = dateparser.parse(start_time)

              # Parse end time
              end = dateparser.parse(end_time) if end_time else datetime.now()

              return {
                  "start_time": start.isoformat(),
                  "end_time": end.isoformat()
              }
        inputs:
          start_time: "${{ inputs.start_time }}"
          end_time: "${{ inputs.end_time }}"

    - ref: "search_logs"
      action: "custom.your_siem.search_logs"
      args:
        query: "${{ inputs.query }}"
        start_time: "${{ steps.parse_time_range.result.start_time }}"
        end_time: "${{ steps.parse_time_range.result.end_time }}"
        limit: "${{ inputs.limit }}"
      depends_on: ["parse_time_range"]

  returns: "${{ steps.search_logs.result }}"
```

**Load custom templates**:

```python
# tracecat/registry/loader.py (modify)

def load_custom_templates():
    """Load custom templates từ custom directory."""

    custom_template_dir = Path("custom/templates")

    if custom_template_dir.exists():
        for template_file in custom_template_dir.rglob("*.yaml"):
            # Parse YAML
            with open(template_file) as f:
                template_data = yaml.safe_load(f)

            # Register action
            registry.register_template(template_data)
```

### 2.3. OAuth Integration

**Example: Custom OAuth provider**

```python
# custom/integrations/oauth_provider.py

from authlib.integrations.httpx_client import AsyncOAuth2Client
from tracecat.integrations.oauth import OAuthProvider

class YourOAuthProvider(OAuthProvider):
    """OAuth provider for your internal system."""

    name = "your_oauth_provider"
    authorization_url = "https://auth.yourcompany.com/oauth/authorize"
    token_url = "https://auth.yourcompany.com/oauth/token"
    default_scopes = ["read:user", "write:data"]

    async def get_user_info(self, access_token: str) -> dict:
        """Fetch user info với access token."""

        async with AsyncOAuth2Client(token=access_token) as client:
            response = await client.get(
                "https://auth.yourcompany.com/api/v1/user"
            )
            return response.json()

# Register provider
from tracecat.integrations.registry import register_oauth_provider

register_oauth_provider(YourOAuthProvider())
```

---

## 3. Tạo Custom Actions

### 3.1. Core Action Types

#### HTTP Request Action
```yaml
- ref: "call_api"
  action: "core.http_request"
  args:
    url: "https://api.example.com/endpoint"
    method: "POST"
    headers:
      Authorization: "Bearer ${{ SECRETS.api.TOKEN }}"
      Content-Type: "application/json"
    payload:
      key: "${{ inputs.value }}"
    params:
      limit: 100
    timeout: 30
```

#### Python Script Action
```yaml
- ref: "process_data"
  action: "core.script.run_python"
  args:
    script: |
      def main(data, threshold):
          """Process and filter data."""
          filtered = [
              item for item in data
              if item['score'] > threshold
          ]

          return {
              "total": len(data),
              "filtered": len(filtered),
              "results": filtered
          }
    inputs:
      data: "${{ steps.fetch_data.result }}"
      threshold: 50
```

#### Conditional Logic
```yaml
- ref: "check_threshold"
  action: "core.script.run_python"
  args:
    script: |
      def main(count, threshold):
          if count > threshold:
              return {"action": "alert", "priority": "high"}
          elif count > threshold / 2:
              return {"action": "monitor", "priority": "medium"}
          else:
              return {"action": "ignore", "priority": "low"}
    inputs:
      count: "${{ steps.count_events.result.total }}"
      threshold: 100

- ref: "send_alert"
  action: "tools.slack.send_message"
  args:
    channel: "#security-alerts"
    text: "High volume detected: ${{ steps.count_events.result.total }} events"
  run_if: "${{ steps.check_threshold.result.action == 'alert' }}"
  depends_on: ["check_threshold"]
```

### 3.2. Complex Action Template

**Example: Phishing Email Investigation**

```yaml
# custom/templates/workflows/investigate_phishing.yaml

type: action
definition:
  title: "Investigate phishing email"
  description: "Comprehensive phishing email investigation workflow"
  namespace: "custom.workflows"
  name: "investigate_phishing"

  expects:
    email_content:
      type: str
      description: "Raw email content (EML format)"
    reporter_email:
      type: str
      description: "Email của người report"

  returns:
    type: object
    description: "Investigation results"

  steps:
    # 1. Parse email
    - ref: "parse_email"
      action: "tools.email.parse"
      args:
        email_content: "${{ inputs.email_content }}"

    # 2. Extract IOCs (Indicators of Compromise)
    - ref: "extract_iocs"
      action: "tools.ioc.extract"
      args:
        text: "${{ steps.parse_email.result.body }}"
        headers: "${{ steps.parse_email.result.headers }}"
      depends_on: ["parse_email"]

    # 3. Parallel reputation checks
    - ref: "check_urls"
      action: "core.workflow.loop"
      args:
        for_each: "${{ steps.extract_iocs.result.urls }}"
        actions:
          - ref: "check_url_reputation"
            action: "tools.virustotal.scan_url"
            args:
              url: "${{ item }}"
      loop_strategy: "parallel"
      depends_on: ["extract_iocs"]

    - ref: "check_ips"
      action: "core.workflow.loop"
      args:
        for_each: "${{ steps.extract_iocs.result.ips }}"
        actions:
          - ref: "check_ip_reputation"
            action: "tools.abuseipdb.check_ip"
            args:
              ip: "${{ item }}"
      loop_strategy: "parallel"
      depends_on: ["extract_iocs"]

    # 4. Check email authentication
    - ref: "check_email_auth"
      action: "core.script.run_python"
      args:
        script: |
          def main(headers):
              # Check SPF, DKIM, DMARC
              spf = headers.get('Received-SPF', 'none')
              dkim = headers.get('DKIM-Signature', None)
              dmarc = headers.get('Authentication-Results', '')

              return {
                  "spf_pass": 'pass' in spf.lower(),
                  "dkim_present": dkim is not None,
                  "dmarc_pass": 'dmarc=pass' in dmarc.lower(),
                  "authenticated": all([
                      'pass' in spf.lower(),
                      dkim is not None,
                      'dmarc=pass' in dmarc.lower()
                  ])
              }
        inputs:
          headers: "${{ steps.parse_email.result.headers }}"
      depends_on: ["parse_email"]

    # 5. Calculate risk score
    - ref: "calculate_risk"
      action: "core.script.run_python"
      args:
        script: |
          def main(url_results, ip_results, email_auth):
              score = 0

              # URL reputation
              malicious_urls = sum(
                  1 for r in url_results
                  if r.get('malicious', 0) > 0
              )
              score += malicious_urls * 30

              # IP reputation
              malicious_ips = sum(
                  1 for r in ip_results
                  if r.get('abuseConfidenceScore', 0) > 75
              )
              score += malicious_ips * 25

              # Email authentication
              if not email_auth['authenticated']:
                  score += 20

              # Determine severity
              if score >= 70:
                  severity = 'critical'
              elif score >= 50:
                  severity = 'high'
              elif score >= 30:
                  severity = 'medium'
              else:
                  severity = 'low'

              return {
                  "risk_score": min(score, 100),
                  "severity": severity,
                  "malicious_urls": malicious_urls,
                  "malicious_ips": malicious_ips,
                  "authenticated": email_auth['authenticated']
              }
        inputs:
          url_results: "${{ steps.check_urls.result }}"
          ip_results: "${{ steps.check_ips.result }}"
          email_auth: "${{ steps.check_email_auth.result }}"
      depends_on: ["check_urls", "check_ips", "check_email_auth"]

    # 6. Create case
    - ref: "create_case"
      action: "core.case.create"
      args:
        title: "Phishing: ${{ steps.parse_email.result.subject }}"
        priority: "${{ steps.calculate_risk.result.severity }}"
        severity: "${{ steps.calculate_risk.result.severity }}"
        status: "open"
        fields:
          reporter: "${{ inputs.reporter_email }}"
          sender: "${{ steps.parse_email.result.from }}"
          subject: "${{ steps.parse_email.result.subject }}"
          risk_score: "${{ steps.calculate_risk.result.risk_score }}"
          iocs: "${{ steps.extract_iocs.result }}"
          url_reputation: "${{ steps.check_urls.result }}"
          ip_reputation: "${{ steps.check_ips.result }}"
          email_auth: "${{ steps.check_email_auth.result }}"
      depends_on: ["calculate_risk"]

    # 7. Notify security team (nếu high/critical)
    - ref: "notify_team"
      action: "tools.slack.send_message"
      args:
        channel: "#security-alerts"
        text: |
          :warning: **High-Risk Phishing Email Detected**

          **Subject:** ${{ steps.parse_email.result.subject }}
          **From:** ${{ steps.parse_email.result.from }}
          **Risk Score:** ${{ steps.calculate_risk.result.risk_score }}/100
          **Severity:** ${{ steps.calculate_risk.result.severity }}

          **Case ID:** ${{ steps.create_case.result.id }}

          **IOCs:**
          - Malicious URLs: ${{ steps.calculate_risk.result.malicious_urls }}
          - Malicious IPs: ${{ steps.calculate_risk.result.malicious_ips }}
          - Email Authenticated: ${{ steps.check_email_auth.result.authenticated }}

          View case: ${{ ENV.PUBLIC_APP_URL }}/cases/${{ steps.create_case.result.id }}
      run_if: "${{ steps.calculate_risk.result.severity in ['high', 'critical'] }}"
      depends_on: ["create_case"]

  returns:
    case_id: "${{ steps.create_case.result.id }}"
    risk_score: "${{ steps.calculate_risk.result.risk_score }}"
    severity: "${{ steps.calculate_risk.result.severity }}"
    iocs: "${{ steps.extract_iocs.result }}"
```

---

## 4. Mở rộng Database Schema

### 4.1. Tạo Custom Models

```python
# custom/schemas/models.py

from sqlalchemy import Column, String, JSON, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from tracecat.db.models import Base, TimestampMixin
import uuid

class ThreatIntelligence(Base, TimestampMixin):
    """Custom threat intelligence table."""

    __tablename__ = "custom_threat_intelligence"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ioc_type = Column(String, nullable=False)  # ip, domain, url, hash
    ioc_value = Column(String, nullable=False, index=True)
    threat_level = Column(String)  # low, medium, high, critical
    source = Column(String)  # virustotal, abuseipdb, internal
    metadata = Column(JSON)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"))

    __table_args__ = (
        # Unique constraint
        {"unique": ("ioc_value", "workspace_id")},
    )


class IncidentPlaybook(Base, TimestampMixin):
    """Pre-defined incident response playbooks."""

    __tablename__ = "custom_incident_playbooks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    incident_type = Column(String, nullable=False)  # phishing, malware, breach
    workflow_id = Column(UUID(as_uuid=True), ForeignKey("workflows.id"))
    description = Column(Text)
    sla_hours = Column(Integer)  # SLA in hours
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"))


class SIEMLog(Base, TimestampMixin):
    """Store SIEM log queries for audit."""

    __tablename__ = "custom_siem_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query = Column(Text, nullable=False)
    result_count = Column(Integer)
    execution_time_ms = Column(Integer)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"))
```

### 4.2. Create Migration

```bash
# Generate migration
export TRACECAT__DB_URI=postgresql+psycopg://postgres:postgres@localhost:5432/postgres
alembic revision --autogenerate -m "Add custom threat intelligence tables"
```

**Generated migration**:

```python
# custom/schemas/migrations/xxxx_add_custom_tables.py

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import uuid

revision = 'xxxx'
down_revision = 'yyyy'  # Previous migration

def upgrade():
    # Threat Intelligence table
    op.create_table(
        'custom_threat_intelligence',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column('ioc_type', sa.String(), nullable=False),
        sa.Column('ioc_value', sa.String(), nullable=False),
        sa.Column('threat_level', sa.String()),
        sa.Column('source', sa.String()),
        sa.Column('metadata', postgresql.JSON()),
        sa.Column('workspace_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workspaces.id')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )

    # Create indexes
    op.create_index(
        'ix_custom_threat_intelligence_ioc_value',
        'custom_threat_intelligence',
        ['ioc_value']
    )

    # Create unique constraint
    op.create_unique_constraint(
        'uq_threat_intel_ioc_workspace',
        'custom_threat_intelligence',
        ['ioc_value', 'workspace_id']
    )

    # Incident Playbook table
    op.create_table(
        'custom_incident_playbooks',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('incident_type', sa.String(), nullable=False),
        sa.Column('workflow_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workflows.id')),
        sa.Column('description', sa.Text()),
        sa.Column('sla_hours', sa.Integer()),
        sa.Column('workspace_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workspaces.id')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )

    # SIEM Log table
    op.create_table(
        'custom_siem_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('query', sa.Text(), nullable=False),
        sa.Column('result_count', sa.Integer()),
        sa.Column('execution_time_ms', sa.Integer()),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id')),
        sa.Column('workspace_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workspaces.id')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table('custom_siem_logs')
    op.drop_table('custom_incident_playbooks')
    op.drop_table('custom_threat_intelligence')
```

```bash
# Run migration
alembic upgrade head
```

### 4.3. Create Service Layer

```python
# custom/api/services/threat_intel.py

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import List
from uuid import UUID

from custom.schemas.models import ThreatIntelligence
from tracecat.auth.types import Role

class ThreatIntelService:
    """Service for threat intelligence management."""

    def __init__(self, session: AsyncSession, role: Role):
        self.session = session
        self.role = role

    async def add_ioc(
        self,
        ioc_type: str,
        ioc_value: str,
        threat_level: str,
        source: str,
        metadata: dict | None = None,
    ) -> ThreatIntelligence:
        """Add IOC to threat intelligence database."""

        ioc = ThreatIntelligence(
            ioc_type=ioc_type,
            ioc_value=ioc_value,
            threat_level=threat_level,
            source=source,
            metadata=metadata or {},
            workspace_id=self.role.workspace_id,
        )

        self.session.add(ioc)
        await self.session.commit()
        await self.session.refresh(ioc)

        return ioc

    async def lookup_ioc(
        self,
        ioc_value: str,
    ) -> ThreatIntelligence | None:
        """Lookup IOC in database."""

        result = await self.session.execute(
            select(ThreatIntelligence).where(
                and_(
                    ThreatIntelligence.ioc_value == ioc_value,
                    ThreatIntelligence.workspace_id == self.role.workspace_id,
                )
            )
        )

        return result.scalar_one_or_none()

    async def list_iocs(
        self,
        ioc_type: str | None = None,
        threat_level: str | None = None,
        limit: int = 100,
    ) -> List[ThreatIntelligence]:
        """List IOCs with optional filters."""

        query = select(ThreatIntelligence).where(
            ThreatIntelligence.workspace_id == self.role.workspace_id
        )

        if ioc_type:
            query = query.where(ThreatIntelligence.ioc_type == ioc_type)

        if threat_level:
            query = query.where(ThreatIntelligence.threat_level == threat_level)

        query = query.limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())
```

### 4.4. Create API Router

```python
# custom/api/routers/threat_intel.py

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List
from uuid import UUID

from tracecat.db.dependencies import AsyncDBSession
from tracecat.auth.dependencies import get_current_user, CurrentUser
from tracecat.auth.types import Role
from custom.api.services.threat_intel import ThreatIntelService

router = APIRouter(prefix="/threat-intel", tags=["threat-intel"])

class AddIOCRequest(BaseModel):
    ioc_type: str
    ioc_value: str
    threat_level: str
    source: str
    metadata: dict | None = None

class IOCResponse(BaseModel):
    id: UUID
    ioc_type: str
    ioc_value: str
    threat_level: str
    source: str
    metadata: dict
    created_at: str

@router.post("", response_model=IOCResponse)
async def add_ioc(
    request: AddIOCRequest,
    session: AsyncDBSession,
    current_user: CurrentUser,
):
    """Add IOC to threat intelligence database."""

    role = Role(
        user_id=current_user.id,
        workspace_id=request.workspace_id,  # From request or context
    )

    service = ThreatIntelService(session, role)

    ioc = await service.add_ioc(
        ioc_type=request.ioc_type,
        ioc_value=request.ioc_value,
        threat_level=request.threat_level,
        source=request.source,
        metadata=request.metadata,
    )

    return IOCResponse(
        id=ioc.id,
        ioc_type=ioc.ioc_type,
        ioc_value=ioc.ioc_value,
        threat_level=ioc.threat_level,
        source=ioc.source,
        metadata=ioc.metadata,
        created_at=ioc.created_at.isoformat(),
    )

@router.get("/{ioc_value}", response_model=IOCResponse | None)
async def lookup_ioc(
    ioc_value: str,
    session: AsyncDBSession,
    current_user: CurrentUser,
):
    """Lookup IOC."""

    role = Role(user_id=current_user.id, workspace_id=...)

    service = ThreatIntelService(session, role)
    ioc = await service.lookup_ioc(ioc_value)

    if not ioc:
        return None

    return IOCResponse(**ioc.__dict__)

@router.get("", response_model=List[IOCResponse])
async def list_iocs(
    ioc_type: str | None = None,
    threat_level: str | None = None,
    limit: int = 100,
    session: AsyncDBSession = Depends(),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List IOCs."""

    role = Role(user_id=current_user.id, workspace_id=...)

    service = ThreatIntelService(session, role)
    iocs = await service.list_iocs(
        ioc_type=ioc_type,
        threat_level=threat_level,
        limit=limit,
    )

    return [IOCResponse(**ioc.__dict__) for ioc in iocs]
```

**Register router**:

```python
# tracecat/api/app.py

from custom.api.routers import threat_intel_router

app.include_router(threat_intel_router)
```

---

(Còn tiếp...)
