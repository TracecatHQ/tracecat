# TỔNG QUAN VÀ HƯỚNG DẪN TRACECAT SOAR

## Giới thiệu

Tài liệu này cung cấp hướng dẫn toàn diện để hiểu, customize, và deploy hệ thống Tracecat SOAR thành một giải pháp SOAR độc lập cho tổ chức của bạn.

## Cấu trúc tài liệu

Tài liệu được chia thành các phần sau:

1. **KIEN-TRUC-SOAR.md** - Kiến trúc hệ thống (Phần 1)
   - Tổng quan hệ thống
   - Kiến trúc tổng thể
   - Kiến trúc chi tiết các component
   - Luồng xử lý dữ liệu
   - Database schema
   - Hệ thống workflow và DSL

2. **KIEN-TRUC-SOAR-PHAN-2.md** - Kiến trúc hệ thống (Phần 2)
   - Integration và Registry
   - Bảo mật và Authentication
   - Công nghệ sử dụng

3. **HUONG-DAN-CUSTOM-VA-DEPLOY.md** - Customization
   - Customization cơ bản
   - Tạo custom integrations
   - Tạo custom actions
   - Mở rộng database schema

4. **HUONG-DAN-DEPLOY-PRODUCTION.md** - Deployment
   - Frontend customization
   - Docker packaging
   - Kubernetes deployment
   - Production configuration

---

## Quick Start Guide

### 1. Setup Development Environment

```bash
# Clone repository
git clone https://github.com/TracecatHQ/tracecat.git
cd tracecat

# Tạo custom branch
git checkout -b custom/your-company-soar

# Setup Python environment
uv venv
source .venv/bin/activate
uv sync

# Setup Frontend
cd frontend
pnpm install
cd ..

# Copy environment file
cp .env.example .env
# Edit .env với configurations của bạn

# Start development stack
just dev
```

### 2. Access Services

Sau khi start development stack, các services sẽ available tại:

- **UI**: http://localhost:3000
- **API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs
- **Temporal UI**: http://localhost:8081
- **MinIO Console**: http://localhost:9001
- **Ray Dashboard**: http://localhost:8265

### 3. Tạo First Workflow

1. Truy cập UI tại http://localhost:3000
2. Sign up / Sign in
3. Navigate to Workflows
4. Click "Create Workflow"
5. Sử dụng workflow builder để tạo workflow
6. Test workflow

---

## Kiến trúc tổng quan

### Core Services

```
┌─────────────────────────────────────────────────────────────────┐
│                    USERS / EXTERNAL SYSTEMS                      │
└────────────────────┬────────────────────────────────────────────┘
                     │
              ┌──────▼──────┐
              │   Caddy     │  Reverse Proxy
              │  (Port 80)  │
              └──────┬──────┘
                     │
        ┌────────────┼────────────┐
        │            │            │
┌───────▼────────┐ ┌▼────────┐ ┌▼────────┐
│   UI           │ │  API    │ │         │
│   (Next.js)    │ │ Service │ │         │
│   Port 3000    │ │ Port    │ │         │
└────────────────┘ │ 8000    │ │         │
                   └──┬───────┘ │         │
                      │         │         │
              ┌───────┴─────────┴─────────┐
              │                           │
       ┌──────▼──────┐          ┌────────▼────────┐
       │   Worker    │          │    Executor     │
       │  (Temporal) │          │     (Ray)       │
       │             │          │   Ports: 8000   │
       └──────┬──────┘          │         8265    │
              │                 └────────┬────────┘
              │                          │
    ┌─────────┴──────────────────────────┴──────────┐
    │                                                │
┌───▼──────┐ ┌──────────┐ ┌────────┐ ┌─────────────▼┐
│PostgreSQL│ │ Temporal │ │ MinIO  │ │     Redis     │
│ (Main)   │ │  Server  │ │  (S3)  │ │ (Cache/Queue) │
└──────────┘ └──────────┘ └────────┘ └───────────────┘
```

### Key Components

1. **API Service** (`tracecat/api/`)
   - FastAPI REST API
   - 31+ routers
   - Authentication & Authorization
   - Resource management (workflows, cases, secrets)

2. **Worker Service** (`tracecat/dsl/worker.py`)
   - Temporal workflow worker
   - Long-running workflow execution
   - Automatic retry and error handling

3. **Executor Service** (`tracecat/api/executor.py`)
   - Ray-based distributed computing
   - Action execution engine
   - Expression evaluation (`${{ }}` syntax)

4. **Frontend** (`frontend/`)
   - Next.js 15 with App Router
   - React Query for state management
   - ReactFlow for workflow builder

5. **Infrastructure**
   - PostgreSQL: Main database
   - Temporal: Workflow orchestration
   - MinIO: S3-compatible object storage
   - Redis: Cache, rate limiting, queues

---

## Key Concepts

### 1. Workflows

Workflows là chuỗi các actions được kết nối với nhau để thực hiện một task tự động.

**Example: Phishing Investigation Workflow**

```yaml
title: "Investigate Phishing Email"
entrypoint: "parse_email"

actions:
  - ref: "parse_email"
    action: "tools.email.parse"
    args:
      email_content: "${{ INPUTS.email }}"

  - ref: "extract_iocs"
    action: "tools.ioc.extract"
    args:
      text: "${{ steps.parse_email.result.body }}"
    depends_on: ["parse_email"]

  - ref: "check_virustotal"
    action: "tools.virustotal.check_ip"
    args:
      ip_addresses: "${{ steps.extract_iocs.result.ips }}"
    depends_on: ["extract_iocs"]

  - ref: "create_case"
    action: "core.case.create"
    args:
      title: "Phishing: ${{ steps.parse_email.result.subject }}"
      priority: "high"
      fields:
        indicators: "${{ steps.extract_iocs.result }}"
    depends_on: ["check_virustotal"]
```

### 2. Actions

Actions là building blocks của workflows. Có 2 types:

**Core actions**: Built-in actions
- `core.http_request`: HTTP API calls
- `core.script.run_python`: Python scripts
- `core.case.create`: Case management
- `core.workflow.execute`: Child workflows

**Integration actions**: From registry
- `tools.virustotal.check_ip`: VirusTotal IP check
- `tools.slack.send_message`: Slack notification
- `tools.okta.suspend_user`: Okta user management

### 3. Expressions

Template expressions `${{ }}` cho phép dynamic data access:

```yaml
# Access inputs
${{ INPUTS.user_id }}

# Access secrets
${{ SECRETS.api_key.API_KEY }}

# Access previous results
${{ steps.fetch_data.result.items[0].id }}

# Functions
${{ FN.to_base64("hello") }}
${{ FN.format("{} - {}", "Item", 123) }}

# Logic
${{ inputs.enabled && inputs.verified }}
${{ inputs.login || inputs.email }}
```

### 4. Triggers

3 ways để trigger workflows:

**Webhooks**:
```bash
POST https://yoursoar.company.com/webhooks/my-webhook
X-Tracecat-Signature: sha256=...
{
  "alert": {
    "id": "123",
    "severity": "high"
  }
}
```

**Schedules** (Cron):
```yaml
triggers:
  - type: schedule
    cron: "0 9 * * MON-FRI"  # 9 AM weekdays
```

**Manual**:
- Via UI: Click "Run" button
- Via API: POST `/workflows/{id}/executions`

### 5. Cases

Cases là incidents hoặc tickets trong hệ thống SOAR:

```python
# Create case
case = await case_service.create_case(
    title="Phishing Email Detected",
    priority="high",
    severity="medium",
    status="open",
    fields={
        "affected_user": "user@company.com",
        "email_subject": "Urgent: Reset Password",
        "indicators": {
            "ips": ["1.2.3.4"],
            "urls": ["http://malicious.com"]
        }
    }
)

# Add comment
await case_service.add_comment(
    case_id=case.id,
    text="Investigating email headers..."
)

# Update status
await case_service.update_case(
    case_id=case.id,
    status="in_progress"
)
```

---

## Customization Strategies

### Strategy 1: Add Custom Integrations

Tạo integrations với internal systems của bạn:

```python
# custom/integrations/your_siem.py

from tracecat_registry import registry, RegistrySecret

@registry.register(
    namespace="custom.your_siem",
    description="Query logs from your SIEM",
    secrets=["your_siem"],
)
async def query_logs(
    query: str,
    start_time: str,
    end_time: str,
    your_siem: RegistrySecret,
) -> dict:
    """Query SIEM logs."""
    # Implementation
    ...
```

### Strategy 2: Extend Database Schema

Add custom tables for your use cases:

```python
# custom/schemas/models.py

class ThreatIntelligence(Base):
    """Store threat intelligence data."""
    __tablename__ = "custom_threat_intelligence"

    id = Column(UUID, primary_key=True)
    ioc_value = Column(String, index=True)
    threat_level = Column(String)
    metadata = Column(JSON)
```

### Strategy 3: Custom UI Components

Build custom dashboards and views:

```typescript
// frontend/src/app/dashboard/page.tsx

export default function SecurityDashboard() {
  const { data } = useQuery({
    queryKey: ["metrics"],
    queryFn: fetchMetrics,
  })

  return (
    <div>
      <MetricCard title="Active Cases" value={data.activeCases} />
      <MetricCard title="Critical Alerts" value={data.criticalAlerts} />
      <IncidentChart data={data.incidents} />
    </div>
  )
}
```

### Strategy 4: Pre-built Workflows

Package workflows cho common use cases:

```yaml
# custom/workflows/phishing_investigation.yaml

title: "Automated Phishing Investigation"
description: "Comprehensive phishing email analysis"
# ... workflow definition
```

---

## Deployment Strategies

### Option 1: Docker Compose (Recommended for small/medium deployments)

**Pros**:
- Easy setup
- All-in-one deployment
- Good for single server

**Cons**:
- Limited scalability
- No high availability

**Usage**:
```bash
docker compose -f deployment/docker/docker-compose.prod.yml up -d
```

### Option 2: Kubernetes (Recommended for large deployments)

**Pros**:
- Highly scalable
- High availability
- Auto-healing
- Rolling updates

**Cons**:
- More complex setup
- Requires K8s knowledge

**Usage**:
```bash
kubectl apply -f deployment/kubernetes/base/
```

### Option 3: Managed Cloud Services

**Architecture**:
- **Compute**: ECS/EKS (AWS), Cloud Run/GKE (GCP), ACI/AKS (Azure)
- **Database**: RDS PostgreSQL, Cloud SQL, Azure Database
- **Storage**: S3, GCS, Azure Blob Storage
- **Cache**: ElastiCache Redis, Memorystore, Azure Cache

**Benefits**:
- Managed infrastructure
- Auto-scaling
- Backups
- Monitoring

---

## Security Best Practices

### 1. Secrets Management

❌ **NEVER** hardcode secrets:
```python
# BAD
api_key = "sk-1234567890abcdef"
```

✅ **Use Tracecat secrets**:
```yaml
# GOOD
headers:
  Authorization: "Bearer ${{ SECRETS.api.API_KEY }}"
```

✅ **Use external secret managers** (production):
- AWS Secrets Manager
- HashiCorp Vault
- Azure Key Vault
- Kubernetes Secrets + Sealed Secrets

### 2. Database Encryption

```bash
# Generate encryption key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Set in environment
TRACECAT__DB_ENCRYPTION_KEY=your-generated-key
```

### 3. HTTPS/TLS

Always use HTTPS in production:

```caddyfile
yoursoar.company.com {
    # Automatic HTTPS with Let's Encrypt
    reverse_proxy api:8000
}
```

### 4. Authentication

Enable multiple auth methods:

```bash
TRACECAT__AUTH_TYPES=basic,google_oauth,saml

# Domain whitelist
TRACECAT__AUTH_ALLOWED_DOMAINS=yourcompany.com,partner.com
```

### 5. Network Security

- Use private networks for services
- Restrict public access to UI/API only
- Use IP whitelisting for admin endpoints
- Enable WAF (Web Application Firewall)

---

## Performance Optimization

### 1. Database

**Connection pooling**:
```bash
TRACECAT__DB_POOL_SIZE=20
TRACECAT__DB_MAX_OVERFLOW=100
```

**Indexes**:
```sql
CREATE INDEX idx_workflows_workspace ON workflows(workspace_id);
CREATE INDEX idx_cases_status ON cases(status);
CREATE INDEX idx_secrets_name ON secrets(name, workspace_id);
```

### 2. Caching

**Redis configuration**:
```bash
REDIS_URL=redis://redis:6379
TRACECAT__CACHE_TTL=300  # 5 minutes
```

**Cache strategies**:
- Workflow definitions
- User permissions
- Secret lookups (decrypted)

### 3. Horizontal Scaling

**API Service**: Scale to handle more requests
```yaml
# Kubernetes
replicas: 5

# Docker Compose
deploy:
  replicas: 3
```

**Worker Service**: Scale for more workflows
```yaml
replicas: 10  # More workers = more parallel workflows
```

**Executor Service**: Scale for action execution
```yaml
replicas: 3
```

### 4. Async Processing

Use async workflows cho long-running tasks:

```yaml
- ref: "bulk_operation"
  action: "core.workflow.loop"
  args:
    for_each: "${{ inputs.items }}"
    actions:
      - ref: "process_item"
        action: "custom.process"
    loop_strategy: "parallel"  # Parallel execution
```

---

## Monitoring và Observability

### 1. Logging

**Structured logging với Loguru**:
```python
logger.bind(
    user_id=user.id,
    workflow_id=workflow.id,
    action="create_case"
).info("Case created", case_id=case.id)
```

**Log aggregation**:
- ELK Stack (Elasticsearch, Logstash, Kibana)
- Grafana Loki
- AWS CloudWatch
- Google Cloud Logging

### 2. Metrics

**Key metrics to monitor**:

- **API**:
  - Request rate
  - Response time (p50, p95, p99)
  - Error rate (4xx, 5xx)

- **Workflows**:
  - Execution count
  - Success/failure rate
  - Average execution time

- **Database**:
  - Connection pool usage
  - Query latency
  - Deadlocks

- **System**:
  - CPU usage
  - Memory usage
  - Disk I/O

**Tools**:
- Prometheus + Grafana
- Datadog
- New Relic
- AWS CloudWatch

### 3. Tracing

**Temporal UI**:
- Workflow execution history
- Event timeline
- Stack traces
- Retry attempts

**Distributed tracing** (optional):
- OpenTelemetry
- Jaeger
- Zipkin

### 4. Alerting

**Setup alerts for**:

- High error rate (> 5%)
- Slow response time (p95 > 1s)
- Database connection pool exhausted
- Worker queue backup (> 100 pending)
- Disk usage > 80%
- Memory usage > 90%

**Alert channels**:
- Slack
- PagerDuty
- Email
- SMS

---

## Backup và Disaster Recovery

### 1. Database Backup

**Automated backups**:
```bash
#!/bin/bash
# deployment/scripts/backup.sh

DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/backups/postgres"
DB_NAME="tracecat"

# Backup
pg_dump -h postgres -U postgres $DB_NAME | gzip > $BACKUP_DIR/backup_$DATE.sql.gz

# Keep only last 30 days
find $BACKUP_DIR -name "backup_*.sql.gz" -mtime +30 -delete
```

**Scheduled backups** (cron):
```bash
0 2 * * * /app/scripts/backup.sh
```

### 2. S3 Backup (MinIO)

```bash
#!/bin/bash
# Backup MinIO data

mc mirror minio/case-attachments s3://backup-bucket/case-attachments/
```

### 3. Restore Procedure

```bash
#!/bin/bash
# deployment/scripts/restore.sh

BACKUP_FILE=$1

# Stop services
docker compose stop api worker executor

# Restore database
gunzip -c $BACKUP_FILE | psql -h postgres -U postgres tracecat

# Start services
docker compose start api worker executor
```

### 4. Disaster Recovery Plan

1. **Backup verification**: Test restores weekly
2. **Multi-region**: Deploy in multiple regions
3. **Documentation**: Keep runbooks updated
4. **Practice**: Run DR drills quarterly

---

## Common Use Cases

### Use Case 1: Phishing Email Investigation

**Workflow**:
1. Parse email (headers, body, attachments)
2. Extract IOCs (IPs, domains, URLs, hashes)
3. Check reputation (VirusTotal, AbuseIPDB)
4. Check email authentication (SPF, DKIM, DMARC)
5. Calculate risk score
6. Create case
7. Notify security team

**Integrations needed**:
- Email parser
- IOC extractors
- VirusTotal
- AbuseIPDB
- Slack/Teams

### Use Case 2: User Offboarding

**Workflow**:
1. Trigger on HR system webhook
2. Disable user in Okta/AD
3. Remove from Google Workspace
4. Remove from Slack
5. Remove from GitHub
6. Archive emails
7. Create case for review
8. Notify IT team

**Integrations needed**:
- Okta/Active Directory
- Google Workspace
- Slack
- GitHub
- Email system

### Use Case 3: Malware Detection Response

**Workflow**:
1. Receive EDR alert
2. Isolate endpoint
3. Collect forensics (memory dump, files)
4. Scan files with antivirus
5. Query SIEM for related events
6. Check threat intelligence
7. Create case
8. Escalate if needed

**Integrations needed**:
- EDR (CrowdStrike, SentinelOne)
- SIEM
- Antivirus
- Threat intelligence feeds

---

## Roadmap Suggestions

Khi customize Tracecat, consider adding:

### Phase 1: Core Customization
- [ ] Custom branding (logo, colors)
- [ ] Internal integrations (SIEM, EDR, IAM)
- [ ] Pre-built workflows for common incidents
- [ ] Custom dashboards

### Phase 2: Enhanced Features
- [ ] Threat intelligence feeds integration
- [ ] SOAR playbook library
- [ ] Automated reporting
- [ ] SLA tracking

### Phase 3: Advanced Features
- [ ] Machine learning for anomaly detection
- [ ] Advanced correlation engine
- [ ] Multi-tenant support (if needed)
- [ ] Compliance reporting (SOC 2, ISO 27001)

### Phase 4: Enterprise Features
- [ ] SSO/SAML integration
- [ ] RBAC enhancements
- [ ] Audit logging
- [ ] API rate limiting
- [ ] Custom retention policies

---

## Support và Resources

### Documentation
- **Architecture**: `docs/vi/KIEN-TRUC-SOAR.md`
- **Integration**: `docs/vi/KIEN-TRUC-SOAR-PHAN-2.md`
- **Customization**: `docs/vi/HUONG-DAN-CUSTOM-VA-DEPLOY.md`
- **Deployment**: `docs/vi/HUONG-DAN-DEPLOY-PRODUCTION.md`

### Official Resources
- **GitHub**: https://github.com/TracecatHQ/tracecat
- **Documentation**: https://docs.tracecat.com
- **Website**: https://tracecat.com

### Community
- **Discord**: Join Tracecat community
- **GitHub Issues**: Report bugs, request features
- **GitHub Discussions**: Ask questions

---

## Conclusion

Tracecat là một nền tảng SOAR mạnh mẽ và linh hoạt. Với kiến trúc modular và khả năng customize cao, bạn có thể:

1. **Customize** hệ thống cho nhu cầu cụ thể
2. **Integrate** với existing tools và systems
3. **Deploy** ở bất kỳ đâu (on-premise, cloud, hybrid)
4. **Scale** theo nhu cầu của organization

Các tài liệu này cung cấp foundation để bạn:
- Hiểu kiến trúc hệ thống
- Customize và extend functionality
- Deploy production-ready SOAR platform
- Maintain và monitor hệ thống

Good luck với SOAR journey của bạn!

---

**Version**: 1.0
**Last Updated**: 2025-01-23
**Author**: Claude Code Analysis
