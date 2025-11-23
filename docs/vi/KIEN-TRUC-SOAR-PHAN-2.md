# TÀI LIỆU KIẾN TRÚC HỆ THỐNG TRACECAT SOAR - PHẦN 2

## 7. Integration và Registry

### 7.1. Registry System

Registry là hệ thống quản lý actions và integrations. Nó là một package độc lập (`tracecat-registry`).

#### Cấu trúc Registry
```
packages/tracecat-registry/
├── tracecat_registry/
│   ├── core/              # Core registry functionality
│   │   ├── __init__.py
│   │   └── registry.py    # Registry loader
│   │
│   ├── integrations/      # Python integration clients
│   │   ├── __init__.py
│   │   ├── amazon_s3.py   # AWS S3 integration
│   │   ├── ansible.py     # Ansible integration
│   │   ├── slack_sdk.py   # Slack integration
│   │   ├── crowdstrike_falconpy.py
│   │   └── ...            # 15+ integrations
│   │
│   └── templates/         # YAML action templates
│       ├── core/          # Core actions
│       │   ├── http_request.yaml
│       │   ├── send_email.yaml
│       │   └── ...
│       │
│       └── tools/         # Integration actions
│           ├── abuseipdb/
│           │   ├── check_ip.yaml
│           │   └── report_ip.yaml
│           │
│           ├── virustotal/
│           │   ├── check_ip.yaml
│           │   ├── check_domain.yaml
│           │   ├── check_file_hash.yaml
│           │   └── scan_url.yaml
│           │
│           ├── okta/
│           │   ├── list_users.yaml
│           │   ├── suspend_user.yaml
│           │   └── ...
│           │
│           └── ...        # 45+ integrations
```

### 7.2. Action Template Format

**Example: VirusTotal IP Check** (`tools/virustotal/check_ip.yaml`)

```yaml
type: action
definition:
  title: "Check IP address"
  description: "Check IP address reputation on VirusTotal"
  display_group: "VirusTotal"
  namespace: "tools.virustotal"
  name: "check_ip"
  expects:  # Input schema
    ip_address:
      type: str
      description: "IP address to check"
  returns:  # Output type
    type: object
    description: "VirusTotal IP report"
  steps:
    - ref: "check_ip_reputation"
      action: "core.http_request"
      args:
        url: "https://www.virustotal.com/api/v3/ip_addresses/${{ FN.url_encode(inputs.ip_address) }}"
        method: "GET"
        headers:
          x-apikey: "${{ SECRETS.virustotal.API_KEY }}"

  returns: "${{ steps.check_ip_reputation.result.data }}"
```

**Components**:

1. **Metadata**:
   - `title`: Tên hiển thị (< 5 words)
   - `description`: Mô tả action
   - `display_group`: Nhóm trong UI
   - `namespace`: Namespace của action (`tools.virustotal`)
   - `name`: Tên action (`check_ip`)

2. **Schema**:
   - `expects`: Input parameters (Pydantic-like schema)
   - `returns`: Output type

3. **Implementation**:
   - `steps`: Danh sách các actions (có thể nested)
   - `returns`: Expression để trả về kết quả

### 7.3. Built-in Core Actions

```yaml
# core.http_request
- action: core.http_request
  args:
    url: "https://api.example.com/data"
    method: "POST"  # GET, POST, PUT, DELETE, PATCH
    headers:
      Authorization: "Bearer ${{ SECRETS.api.TOKEN }}"
    payload:  # JSON body (KHÔNG phải 'json' hoặc 'json_body')
      key: "value"
    params:  # Query parameters
      limit: 100

# core.script.run_python
- action: core.script.run_python
  args:
    script: |
      def main(data):
          # Process data
          return [item for item in data if item['status'] == 'active']
    inputs:
      data: "${{ steps.fetch_data.result }}"

# core.workflow.execute
- action: core.workflow.execute
  args:
    workflow_id: "wf_child_workflow"
    trigger_inputs:
      parent_data: "${{ steps.parent_step.result }}"

# core.case.create
- action: core.case.create
  args:
    title: "Security Incident"
    priority: "high"
    severity: "medium"
    status: "open"
    fields:
      affected_systems: ["server1", "server2"]
      detected_at: "${{ FN.now() }}"

# core.case.update
- action: core.case.update
  args:
    case_id: "${{ INPUTS.case_id }}"
    status: "in_progress"
    fields:
      investigation_notes: "Analysis complete"

# core.case.add_comment
- action: core.case.add_comment
  args:
    case_id: "${{ INPUTS.case_id }}"
    text: "Investigation findings: ${{ steps.analyze.result.summary }}"

# core.table.lookup
- action: core.table.lookup
  args:
    table_name: "ip_allowlist"
    key: "${{ steps.extract.result.ip }}"
    column: "ip_address"

# core.send_email
- action: core.send_email
  args:
    to: ["security@company.com"]
    subject: "Security Alert: ${{ INPUTS.alert_type }}"
    body: "${{ steps.format_report.result }}"
```

### 7.4. Integration Clients (Python)

Một số integrations cần Python client library:

**Example: Slack Integration** (`integrations/slack_sdk.py`)

```python
from slack_sdk import WebClient
from tracecat_registry import RegistrySecret, registry

# Registry decorator để register function
@registry.register(
    namespace="tools.slack",
    description="Send message to Slack channel",
    secrets=["slack"],  # Required secret
)
def send_message(
    channel: str,
    text: str,
    slack: RegistrySecret,  # Auto-injected secret
) -> dict[str, Any]:
    """Send message to Slack channel."""

    client = WebClient(token=slack["BOT_TOKEN"])

    response = client.chat_postMessage(
        channel=channel,
        text=text
    )

    return {
        "ok": response["ok"],
        "ts": response["ts"],
        "channel": response["channel"]
    }

@registry.register(
    namespace="tools.slack",
    description="Upload file to Slack channel",
    secrets=["slack"],
)
def upload_file(
    channels: list[str],
    file_content: bytes,
    filename: str,
    title: str | None = None,
    slack: RegistrySecret = None,
) -> dict[str, Any]:
    """Upload file to Slack channel."""

    client = WebClient(token=slack["BOT_TOKEN"])

    response = client.files_upload_v2(
        channels=channels,
        file=file_content,
        filename=filename,
        title=title,
    )

    return response.data
```

**Usage trong workflow**:
```yaml
- ref: "notify_slack"
  action: "tools.slack.send_message"
  args:
    channel: "#security-alerts"
    text: "New phishing email detected: ${{ INPUTS.subject }}"
```

### 7.5. Registry Loading

**Server startup** (`tracecat/api/app.py:104`):
```python
async def lifespan(app: FastAPI):
    role = bootstrap_role()
    async with get_async_session_context_manager() as session:
        # Reload registry vào database
        await reload_registry(session, role)
```

**Registry reload** (`tracecat/registry/common.py`):
```python
async def reload_registry(session: AsyncSession, role: Role):
    """
    Load tất cả actions từ:
    1. Built-in registry (tracecat-registry package)
    2. Local repository (nếu enabled)
    3. Git repositories (nếu configured)
    """

    # 1. Load từ built-in registry
    from tracecat_registry import registry as builtin_registry

    for action in builtin_registry.actions:
        # Upsert vào registry_actions table
        await upsert_action(
            session=session,
            name=action.name,
            namespace=action.namespace,
            definition=action.to_dict(),
            version=action.version,
        )

    # 2. Load từ local repository
    if config.TRACECAT__LOCAL_REPOSITORY_ENABLED:
        local_actions = load_local_repository(
            config.TRACECAT__LOCAL_REPOSITORY_PATH
        )
        for action in local_actions:
            await upsert_action(session, ...)

    # 3. Load từ Git repositories
    repos = await list_repositories(session, role)
    for repo in repos:
        if repo.origin == "git" and repo.enabled:
            await sync_git_repository(repo)
```

### 7.6. OAuth Integrations

Một số integrations hỗ trợ OAuth 2.0 flow:

**Supported providers**:
- Google (Gmail, Drive, Calendar)
- Microsoft (Office 365, Teams)
- GitHub
- Okta
- Salesforce

**OAuth flow**:
```
1. User initiates OAuth (Frontend)
   │
   ├─→ GET /integrations/providers/{provider}/authorize
   │
2. API redirects to provider
   │
   ├─→ Redirect to: https://provider.com/oauth/authorize
   │       ?client_id=...
   │       &redirect_uri=https://app.tracecat.com/integrations/callback
   │       &scope=read:user,write:repo
   │       &state=random_state
   │
3. User authorizes on provider site
   │
4. Provider redirects back
   │
   ├─→ GET /integrations/callback
   │       ?code=AUTH_CODE
   │       &state=random_state
   │
5. API exchanges code for tokens
   │
   ├─→ POST https://provider.com/oauth/token
   │       {
   │         "client_id": "...",
   │         "client_secret": "...",
   │         "code": "AUTH_CODE",
   │         "grant_type": "authorization_code"
   │       }
   │
   ├─→ Response:
   │       {
   │         "access_token": "...",
   │         "refresh_token": "...",
   │         "expires_in": 3600
   │       }
   │
6. Store encrypted tokens
   │
   └─→ CREATE integration
           {
             "provider": "github",
             "access_token_enc": encrypt(access_token),
             "refresh_token_enc": encrypt(refresh_token),
             "expires_at": now() + 3600
           }
```

**Token refresh**:
```python
async def refresh_oauth_token(integration: Integration):
    """Refresh expired OAuth token."""

    # Decrypt refresh token
    refresh_token = decrypt(integration.refresh_token_enc)

    # Exchange for new tokens
    response = await http_client.post(
        provider.token_url,
        data={
            "client_id": provider.client_id,
            "client_secret": provider.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    )

    # Update integration
    integration.access_token_enc = encrypt(response["access_token"])
    if "refresh_token" in response:
        integration.refresh_token_enc = encrypt(response["refresh_token"])
    integration.expires_at = now() + response["expires_in"]

    await session.commit()
```

---

## 8. Bảo mật và Authentication

### 8.1. Authentication System

#### Supported auth types
```python
# tracecat/auth/enums.py

class AuthType(str, Enum):
    BASIC = "basic"           # Email/password
    GOOGLE_OAUTH = "google_oauth"  # Google OAuth 2.0
    SAML = "saml"             # SAML SSO
```

**Configuration** (`.env`):
```bash
TRACECAT__AUTH_TYPES=basic,google_oauth,saml
```

#### FastAPI Users setup

Tracecat sử dụng `fastapi-users` library:

```python
# tracecat/auth/users.py

from fastapi_users import FastAPIUsers
from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
    JWTStrategy,
)

# Cookie transport (7 days)
cookie_transport = CookieTransport(
    cookie_name="tracecat_auth",
    cookie_max_age=60 * 60 * 24 * 7,  # 7 days
    cookie_secure=True,  # HTTPS only
    cookie_httponly=True,
    cookie_samesite="lax",
)

# JWT strategy
def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=config.USER_AUTH_SECRET,
        lifetime_seconds=60 * 60 * 24 * 7,  # 7 days
        algorithm="HS256",
    )

# Auth backend
auth_backend = AuthenticationBackend(
    name="jwt",
    transport=cookie_transport,
    get_strategy=get_jwt_strategy,
)

# FastAPI Users instance
fastapi_users = FastAPIUsers[User, uuid.UUID](
    get_user_manager,
    [auth_backend],
)
```

#### Registration flow

```
User (Frontend)
    │
    ├─→ POST /auth/register
    │       {
    │         "email": "user@company.com",
    │         "password": "SecurePass123!",
    │         "organization_id": "org_..."
    │       }
    │
API Service
    │
    ├─→ Validate email
    │       - Check allowed domains (if configured)
    │       - Check password length (min: 8 chars default)
    │       - Check if email exists
    │
    ├─→ Create User
    │       user = User(
    │         email="user@company.com",
    │         hashed_password=hash_password("SecurePass123!"),
    │         is_active=True,
    │         is_verified=False,  # Need email verification
    │         organization_id="org_..."
    │       )
    │
    ├─→ Create default workspace membership
    │       membership = Membership(
    │         user_id=user.id,
    │         workspace_id=default_workspace.id,
    │         role="editor"
    │       )
    │
    └─→ Send verification email (if enabled)
            send_verification_email(user.email)
```

#### Login flow

```
User (Frontend)
    │
    ├─→ POST /auth/login
    │       {
    │         "username": "user@company.com",
    │         "password": "SecurePass123!"
    │       }
    │
API Service
    │
    ├─→ Verify credentials
    │       user = get_user_by_email("user@company.com")
    │       verify_password("SecurePass123!", user.hashed_password)
    │
    ├─→ Generate JWT token
    │       token = jwt.encode(
    │         {
    │           "sub": str(user.id),
    │           "email": user.email,
    │           "exp": now() + 7 days
    │         },
    │         secret=USER_AUTH_SECRET,
    │         algorithm="HS256"
    │       )
    │
    ├─→ Set cookie
    │       response.set_cookie(
    │         name="tracecat_auth",
    │         value=token,
    │         max_age=7 days,
    │         httponly=True,
    │         secure=True
    │       )
    │
    └─→ Return user
            { "id": "...", "email": "...", ... }
```

### 8.2. Authorization (RBAC)

#### Role hierarchy

**User roles** (platform-level):
```python
class UserRole(str, Enum):
    BASIC = "basic"           # Regular user
    ADMIN = "admin"           # Platform admin
```

**Workspace roles**:
```python
class WorkspaceRole(str, Enum):
    VIEWER = "viewer"         # Read-only
    EDITOR = "editor"         # Read + Write
    ADMIN = "admin"           # Read + Write + Manage members
    OWNER = "owner"           # Full control
```

#### Permission matrix

| Resource | VIEWER | EDITOR | ADMIN | OWNER |
|----------|--------|--------|-------|-------|
| View workflows | ✓ | ✓ | ✓ | ✓ |
| Create workflows | ✗ | ✓ | ✓ | ✓ |
| Edit workflows | ✗ | ✓ | ✓ | ✓ |
| Delete workflows | ✗ | ✗ | ✓ | ✓ |
| View secrets | ✗ | ✓ | ✓ | ✓ |
| Create secrets | ✗ | ✓ | ✓ | ✓ |
| Manage members | ✗ | ✗ | ✓ | ✓ |
| Delete workspace | ✗ | ✗ | ✗ | ✓ |

#### Authorization check

```python
# tracecat/authz/service.py

async def check_permission(
    session: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    required_permission: Permission,
) -> bool:
    """Check if user has permission in workspace."""

    # Get membership
    membership = await session.execute(
        select(Membership).where(
            Membership.user_id == user_id,
            Membership.workspace_id == workspace_id,
        )
    )
    membership = membership.scalar_one_or_none()

    if not membership:
        return False

    # Check role permissions
    role_permissions = ROLE_PERMISSIONS[membership.role]
    return required_permission in role_permissions

# Usage trong API endpoint
@router.delete("/workflows/{workflow_id}")
async def delete_workflow(
    workflow_id: UUID,
    session: AsyncDBSession,
    current_user: CurrentUser,
):
    # Check permission
    has_permission = await check_permission(
        session,
        user_id=current_user.id,
        workspace_id=workflow.workspace_id,
        required_permission=Permission.DELETE_WORKFLOW,
    )

    if not has_permission:
        raise HTTPException(
            status_code=403,
            detail="Insufficient permissions"
        )

    # Proceed with deletion
    await delete_workflow_service(workflow_id)
```

#### Authorization caching

```python
# tracecat/middleware.py

class AuthorizationCacheMiddleware:
    """Cache authorization results để giảm database queries."""

    async def __call__(self, request: Request, call_next):
        # Cache key: user_id:workspace_id:permission
        cache_key = f"authz:{user_id}:{workspace_id}:{permission}"

        # Check cache
        cached = await redis.get(cache_key)
        if cached:
            return cached == "1"

        # Check database
        has_permission = await check_permission(...)

        # Cache result (5 minutes)
        await redis.setex(
            cache_key,
            300,  # 5 minutes
            "1" if has_permission else "0"
        )

        return await call_next(request)
```

### 8.3. Secret Management

#### Encryption

Secrets được mã hóa với **Fernet** (symmetric encryption):

```python
# tracecat/auth/crypto.py

from cryptography.fernet import Fernet

class SecretEncryption:
    def __init__(self, key: str):
        """
        Args:
            key: Base64-encoded 32-byte key
                 Generate: Fernet.generate_key()
        """
        self.cipher = Fernet(key.encode())

    def encrypt(self, plaintext: dict[str, Any]) -> bytes:
        """Encrypt secret data."""
        # Convert to JSON
        json_data = json.dumps(plaintext)

        # Encrypt
        encrypted = self.cipher.encrypt(json_data.encode())

        return encrypted

    def decrypt(self, ciphertext: bytes) -> dict[str, Any]:
        """Decrypt secret data."""
        # Decrypt
        decrypted = self.cipher.decrypt(ciphertext)

        # Parse JSON
        plaintext = json.loads(decrypted.decode())

        return plaintext

# Usage
encryption = SecretEncryption(config.TRACECAT__DB_ENCRYPTION_KEY)

# Encrypt
secret_data = {"API_KEY": "sk-1234567890abcdef"}
encrypted = encryption.encrypt(secret_data)

# Store in database
secret = Secret(
    name="my_api",
    encrypted_keys=encrypted,
    workspace_id=workspace_id,
)

# Decrypt when needed
decrypted = encryption.decrypt(secret.encrypted_keys)
api_key = decrypted["API_KEY"]
```

#### Secret scopes

**Workspace secrets**:
- Scope: Single workspace
- Access: All users in workspace (based on role)
- Table: `secrets`

**Organization secrets**:
- Scope: All workspaces in organization
- Access: Organization admins only
- Table: `organization_secrets`

**Environment support**:
```python
# Different secrets per environment
secret_dev = Secret(
    name="database",
    environment="development",
    encrypted_keys=...
)

secret_prod = Secret(
    name="database",
    environment="production",
    encrypted_keys=...
)
```

#### Secret rotation

```python
async def rotate_secret(
    session: AsyncSession,
    secret_id: UUID,
    new_keys: dict[str, Any],
    role: Role,
):
    """Rotate secret keys."""

    # Get existing secret
    secret = await get_secret(session, secret_id, role)

    # Backup old secret
    old_secret = Secret(
        name=f"{secret.name}_backup_{int(time.time())}",
        encrypted_keys=secret.encrypted_keys,
        workspace_id=secret.workspace_id,
        environment=secret.environment,
    )
    session.add(old_secret)

    # Update secret
    secret.encrypted_keys = encrypt(new_keys)
    secret.updated_at = datetime.now(UTC)

    await session.commit()
```

### 8.4. API Keys & Service Authentication

#### Service-to-service authentication

```python
# Service key authentication
TRACECAT__SERVICE_KEY=your-secret-service-key

# Usage
headers = {
    "X-Tracecat-Service-Key": config.TRACECAT__SERVICE_KEY
}
```

**Middleware check**:
```python
# tracecat/middleware.py

async def verify_service_key(request: Request):
    """Verify service key từ header."""

    service_key = request.headers.get("X-Tracecat-Service-Key")

    if not service_key:
        raise HTTPException(401, "Missing service key")

    if service_key != config.TRACECAT__SERVICE_KEY:
        raise HTTPException(401, "Invalid service key")
```

#### Webhook signatures

```python
# tracecat/webhooks/crypto.py

import hmac
import hashlib

def sign_webhook_payload(payload: bytes, secret: str) -> str:
    """Generate HMAC-SHA256 signature."""

    signature = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()

    return f"sha256={signature}"

def verify_webhook_signature(
    payload: bytes,
    signature: str,
    secret: str,
) -> bool:
    """Verify webhook signature."""

    expected = sign_webhook_payload(payload, secret)

    # Constant-time comparison
    return hmac.compare_digest(expected, signature)

# Usage trong webhook endpoint
@router.post("/webhooks/{path}")
async def handle_webhook(
    path: str,
    request: Request,
):
    # Get signature
    signature = request.headers.get("X-Tracecat-Signature")

    # Get webhook
    webhook = await get_webhook_by_path(path)

    # Verify signature
    payload = await request.body()
    if not verify_webhook_signature(payload, signature, webhook.secret):
        raise HTTPException(401, "Invalid signature")

    # Process webhook
    ...
```

---

## 9. Công nghệ sử dụng

### 9.1. Backend Stack

#### Core framework
```python
fastapi==0.120.3              # Web framework
uvicorn>=0.33.0               # ASGI server
starlette>=0.49.1             # ASGI toolkit
pydantic>=2.11.7              # Data validation
orjson==3.10.3                # Fast JSON serialization
```

#### Database
```python
sqlalchemy>=2.0.0             # ORM
alembic==1.13.2               # Migrations
asyncpg==0.29.0               # Async PostgreSQL driver
psycopg[binary]==3.1.19       # PostgreSQL adapter
```

#### Workflow orchestration
```python
temporalio==1.17.0            # Temporal Python SDK
```

#### Distributed computing
```python
ray[default]==2.43.0          # Distributed computing framework
```

#### AI/LLM
```python
pydantic-ai-slim[openai,anthropic,bedrock,google,mcp]==1.6.0
# Supports:
# - OpenAI (GPT-4, GPT-3.5)
# - Anthropic (Claude)
# - AWS Bedrock
# - Google (Gemini)
# - MCP (Model Context Protocol)
```

#### Storage
```python
minio==7.2.18                 # S3-compatible object storage
redis[hiredis]>=5.0.0         # Cache, queue, rate limiting
```

#### Authentication
```python
fastapi-users[sqlalchemy,oauth]==14.0.1
authlib==1.6.5                # OAuth client
cryptography==44.0.1          # Encryption
pysaml2==7.5.0                # SAML SSO
```

#### HTTP client
```python
httpx==0.28.1                 # Async HTTP client
tenacity==8.3.0               # Retry logic
```

#### Utilities
```python
loguru==0.7.2                 # Logging
python-slugify==8.0.4         # URL-safe slugs
pyarrow==16.1.0               # Parquet support
jsonpath_ng>=1.7.0            # JSONPath queries
lark==1.1.9                   # Parser for expressions
dateparser>=1.2.1             # Natural language date parsing
```

### 9.2. Frontend Stack

#### Core
```json
{
  "next": "15.5.2",                    // React framework
  "react": "18.3.1",                   // UI library
  "react-dom": "18.3.1",
  "typescript": "5.9.2"                // Type safety
}
```

#### State management
```json
{
  "@tanstack/react-query": "5.90.2",  // Server state
  "zustand": "^4.5.0"                  // Client state (optional)
}
```

#### UI components
```json
{
  "@radix-ui/react-*": "latest",       // Headless components
  "tailwindcss": "3.4.17",             // Utility-first CSS
  "tailwindcss-animate": "^1.0.7",     // Animations
  "class-variance-authority": "^0.7.0", // Component variants
  "clsx": "^2.1.0",                    // Classname utility
  "lucide-react": "^0.453.0"           // Icons
}
```

#### Forms
```json
{
  "react-hook-form": "^7.54.2",        // Form management
  "zod": "^3.24.1",                    // Schema validation
  "@hookform/resolvers": "^3.9.1"      // RHF + Zod integration
}
```

#### Workflow builder
```json
{
  "@xyflow/react": "12.8.6"            // ReactFlow for node-based UI
}
```

#### Rich text editing
```json
{
  "@tiptap/react": "3.7.1",            // Rich text editor
  "@tiptap/starter-kit": "3.7.1",
  "@codemirror/lang-python": "^6.1.6", // Code editor
  "@codemirror/lang-yaml": "^6.1.1"
}
```

#### AI integration
```json
{
  "ai": "5.0.59",                      // Vercel AI SDK
  "@ai-sdk/react": "2.0.59",           // React hooks for AI
  "@ai-sdk/openai": "^2.0.59",
  "@ai-sdk/anthropic": "^2.0.59"
}
```

### 9.3. Infrastructure

#### Docker images
```yaml
# Base images
postgres:16                 # Main database
postgres:13                 # Temporal database
redis:7-alpine             # Cache/queue
minio/minio:RELEASE.2025-05-24T17-08-30Z  # Object storage
temporalio/auto-setup:1.27.1  # Temporal server
temporalio/ui:latest        # Temporal UI
caddy:2.10.2-alpine        # Reverse proxy
```

#### Python package manager
```bash
uv==0.9.7                  # Modern, fast Python package manager
# Features:
# - 10-100x faster than pip
# - Built-in virtual env management
# - Lock file support (uv.lock)
# - Workspace support (monorepo)
```

#### JavaScript package manager
```bash
pnpm                       # Fast, efficient package manager
# Features:
# - Shared node_modules
# - Fast installs
# - Workspace support
```

### 9.4. Development tools

#### Linting & formatting
```python
ruff==0.13.0               # Fast Python linter & formatter
# Replaces:
# - flake8
# - isort
# - black
# - pyupgrade

mypy==1.15.0               # Static type checker
```

```json
{
  "@biomejs/biome": "^1.9.4"  // Fast JS/TS linter & formatter
  // Replaces:
  // - ESLint
  // - Prettier
}
```

#### Testing
```python
pytest==8.3.2              # Testing framework
pytest-asyncio==0.24.0     # Async test support (deprecated, use pytest-anyio)
pytest-anyio==0.24.0       # Async test support (recommended)
pytest-mock==3.14.0        # Mocking
pytest-xdist==3.8.0        # Parallel testing
respx==0.22.0              # HTTP mocking
```

#### Pre-commit hooks
```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    hooks:
      - id: ruff          # Linter
      - id: ruff-format   # Formatter

  - repo: https://github.com/gitleaks/gitleaks
    hooks:
      - id: gitleaks      # Secret scanning

  - repo: https://github.com/pre-commit/pre-commit-hooks
    hooks:
      - id: check-yaml
      - id: check-toml
      - id: end-of-file-fixer
```

### 9.5. Monitoring & Observability

#### Logging
```python
loguru==0.7.2              # Structured logging
# Features:
# - JSON output
# - Context binding
# - Log levels
# - Rotation

# Example
logger.bind(
    user_id=user.id,
    workflow_id=workflow.id,
    action="create_workflow"
).info("Workflow created")
```

#### Error tracking
```python
sentry-sdk==2.24.1         # Error tracking
# Integration với:
# - FastAPI
# - Temporal
# - Ray
```

#### Tracing (optional)
```python
# Langfuse integration (optional)
LANGFUSE_HOST=https://cloud.langfuse.com
LANGFUSE_PUBLIC_KEY=pk-...
LANGFUSE_SECRET_KEY=sk-...

# Traces:
# - LLM calls
# - Agent interactions
# - Workflow execution
```

#### Temporal UI
```bash
# Built-in workflow monitoring
http://localhost:8081

# Features:
# - Workflow history
# - Event timeline
# - Stack traces
# - Retry attempts
# - Search workflows
```

#### Ray Dashboard
```bash
# Built-in Ray cluster monitoring
http://localhost:8265

# Features:
# - Node status
# - Task monitoring
# - Resource usage
# - Actor lifecycle
```

---

**Hết phần 2**

Tiếp tục với phần 3: Hướng dẫn Customization và Deployment...
