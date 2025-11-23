# TÀI LIỆU KIẾN TRÚC HỆ THỐNG TRACECAT SOAR

## Mục lục
1. [Tổng quan hệ thống](#1-tổng-quan-hệ-thống)
2. [Kiến trúc tổng thể](#2-kiến-trúc-tổng-thể)
3. [Kiến trúc chi tiết từng component](#3-kiến-trúc-chi-tiết-từng-component)
4. [Luồng xử lý dữ liệu](#4-luồng-xử-lý-dữ-liệu)
5. [Database schema](#5-database-schema)
6. [Hệ thống workflow và DSL](#6-hệ-thống-workflow-và-dsl)
7. [Integration và Registry](#7-integration-và-registry)
8. [Bảo mật và Authentication](#8-bảo-mật-và-authentication)
9. [Công nghệ sử dụng](#9-công-nghệ-sử-dụng)

---

## 1. Tổng quan hệ thống

### 1.1. Giới thiệu
**Tracecat** là một nền tảng tự động hóa bảo mật (SOAR - Security Orchestration, Automation and Response) mã nguồn mở, được thiết kế cho các kỹ sư bảo mật và IT. Đây là một giải pháp thay thế cho Tines và Splunk SOAR.

### 1.2. Đặc điểm chính
- **Mã nguồn mở**: License AGPL-3.0
- **Kiến trúc microservices**: Tách biệt API, Worker, Executor services
- **Workflow orchestration**: Sử dụng Temporal cho workflow dài hạn
- **Distributed computing**: Ray framework cho xử lý phân tán
- **No-code UI**: Giao diện drag-and-drop với Next.js 15
- **YAML-based templates**: Dễ dàng tạo và chia sẻ workflows
- **Built-in integrations**: 45+ tích hợp sẵn có
- **Case management**: Quản lý incident và case
- **AI-powered**: Tích hợp AI agents với pydantic-ai

### 1.3. Thống kê codebase
- **Backend**: 37,417+ dòng Python code
- **Frontend**: 580+ TypeScript files
- **Database models**: 43+ SQLAlchemy models
- **API routers**: 31+ FastAPI routers
- **Integrations**: 45+ integration templates
- **Tests**: Comprehensive test coverage (unit, integration, regression)

---

## 2. Kiến trúc tổng thể

### 2.1. Sơ đồ kiến trúc

```
┌─────────────────────────────────────────────────────────────────┐
│                        EXTERNAL ACCESS                           │
│                    (Caddy Reverse Proxy)                         │
└────────────────────┬────────────────────────────────────────────┘
                     │
        ┌────────────┴────────────┐
        │                         │
┌───────▼────────┐        ┌──────▼───────┐
│   UI (Next.js) │        │  API Service │
│   Port: 3000   │◄───────┤  Port: 8000  │
└────────────────┘        └──────┬───────┘
                                 │
                    ┌────────────┼────────────┐
                    │            │            │
            ┌───────▼──────┐ ┌──▼─────────┐ │
            │ Worker       │ │ Executor   │ │
            │ (Temporal)   │ │ (Ray)      │ │
            │              │ │ Port: 8000 │ │
            └───────┬──────┘ └──┬─────────┘ │
                    │           │           │
        ┌───────────┴───────────┴───────────┴──────────┐
        │                                               │
┌───────▼────────┐  ┌──────────┐  ┌────────┐  ┌───────▼───────┐
│   PostgreSQL   │  │ Temporal │  │ MinIO  │  │     Redis     │
│   (Main DB)    │  │ Server   │  │ (S3)   │  │ (Cache/Queue) │
│   Port: 5432   │  │ :7233    │  │ :9000  │  │   Port: 6379  │
└────────────────┘  └──────────┘  └────────┘  └───────────────┘
```

### 2.2. Các service chính

#### **API Service** (`tracecat/api/`)
- **Vai trò**: HTTP API server, điểm vào chính của hệ thống
- **Framework**: FastAPI với ORJSON để tối ưu performance
- **Port**: 8000
- **Chức năng**:
  - Xử lý authentication/authorization
  - Quản lý workflows, actions, schedules
  - CRUD operations cho tất cả resources
  - Webhook endpoints
  - Case management API
  - Integration management

#### **Worker Service** (`tracecat/dsl/worker.py`)
- **Vai trò**: Temporal workflow worker, thực thi workflows
- **Framework**: Temporal.io Python SDK
- **Chức năng**:
  - Chạy DSL workflows như Temporal workflows
  - Xử lý long-running workflows
  - Retry và error handling tự động
  - Schedule management
  - Interaction với Executor để chạy actions

#### **Executor Service** (`tracecat/api/executor.py`)
- **Vai trò**: Distributed action execution engine
- **Framework**: Ray + FastAPI
- **Ports**: 8000 (API), 8265 (Dashboard)
- **Chức năng**:
  - Thực thi actions từ workflows
  - Distributed computing với Ray
  - Expression evaluation (template `${{ }}` syntax)
  - Rate limiting
  - Resource management

#### **Frontend (UI)** (`frontend/`)
- **Vai trò**: User interface
- **Framework**: Next.js 15 với App Router
- **Port**: 3000
- **Chức năng**:
  - Workflow builder (drag-and-drop)
  - Case management UI
  - Integration configuration
  - Secret management
  - User/workspace management
  - AI agent interface

### 2.3. Infrastructure services

#### **PostgreSQL**
- **Main database**: Lưu trữ tất cả application data
- **Temporal database**: Riêng cho Temporal server
- **Schema migration**: Alembic
- **Connection pooling**: SQLAlchemy async engine

#### **Temporal Server**
- **Workflow orchestration**: Quản lý workflow state
- **Durability**: Workflows có thể chạy hàng giờ/ngày
- **Visibility**: UI để monitoring workflows
- **Port**: 7233 (gRPC), 8081 (UI)

#### **MinIO**
- **Object storage**: S3-compatible
- **Use cases**: Case attachments, exports, files
- **Ports**: 9000 (API), 9001 (Console)

#### **Redis**
- **Caching**: Kết quả queries, sessions
- **Rate limiting**: Token bucket algorithm
- **Port**: 6379

---

## 3. Kiến trúc chi tiết từng component

### 3.1. API Service Architecture

#### Cấu trúc thư mục
```
tracecat/api/
├── app.py                    # Main FastAPI application
├── executor.py               # Executor service app
├── common.py                 # Shared utilities
└── routers/                  # (Distributed across modules)
    ├── workflows/            # Workflow management
    ├── actions/              # Action management
    ├── cases/                # Case management
    ├── webhooks/             # Webhook handling
    ├── secrets/              # Secret management
    ├── auth/                 # Authentication
    └── ...
```

#### Application lifecycle (`app.py:89-111`)
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Thêm Temporal search attributes
    asyncio.create_task(add_temporal_search_attributes())

    # 2. Tạo MinIO bucket cho attachments
    await ensure_bucket_exists(config.TRACECAT__BLOB_STORAGE_BUCKET_ATTACHMENTS)

    # 3. Bootstrap admin role
    role = bootstrap_role()

    async with get_async_session_context_manager() as session:
        # 4. Setup organization settings
        await setup_org_settings(session, role)

        # 5. Reload registry (integrations)
        await reload_registry(session, role)

        # 6. Tạo default workspace nếu chưa có
        await setup_workspace_defaults(session, role)

    yield
```

#### Routers (31+ routers)
Tất cả routers được include trong `app.py:206-253`:
```python
# Core workflow routers
app.include_router(workflow_management_router)    # CRUD workflows
app.include_router(workflow_executions_router)    # Run workflows
app.include_router(workflow_actions_router)       # Action management
app.include_router(schedules_router)              # Cron schedules

# Trigger routers
app.include_router(webhook_router)                # Webhooks

# Resource routers
app.include_router(secrets_router)                # Secrets
app.include_router(variables_router)              # Variables
app.include_router(tables_router)                 # Lookup tables

# Case management routers
app.include_router(cases_router)                  # Cases
app.include_router(case_fields_router)            # Custom fields
app.include_router(case_tags_router)              # Tags
app.include_router(case_attachments_router)       # Files

# Integration routers
app.include_router(registry_actions_router)       # Action registry
app.include_router(registry_repos_router)         # Repositories
app.include_router(integrations_router)           # OAuth integrations

# AI/Agent routers
app.include_router(agent_router)                  # AI agents
app.include_router(chat_router)                   # Chat interface

# Admin routers
app.include_router(users_router)                  # Users
app.include_router(workspaces_router)             # Workspaces
app.include_router(org_router)                    # Organization
```

#### Middleware stack (`app.py:324-336`)
```python
# 1. AuthorizationCacheMiddleware: Cache permissions
app.add_middleware(AuthorizationCacheMiddleware)

# 2. RequestLoggingMiddleware: Log tất cả requests
app.add_middleware(RequestLoggingMiddleware)

# 3. SecurityHeadersMiddleware: CSP, HSTS, etc. (production only)
if config.TRACECAT__APP_ENV != "development":
    app.add_middleware(SecurityHeadersMiddleware)

# 4. CORSMiddleware: CORS headers
app.add_middleware(CORSMiddleware, ...)
```

#### Exception handlers
```python
# Generic exceptions
app.add_exception_handler(Exception, generic_exception_handler)

# Tracecat exceptions
app.add_exception_handler(TracecatException, tracecat_exception_handler)

# Validation errors (422)
app.add_exception_handler(RequestValidationError, validation_exception_handler)

# FastAPI Users auth errors
app.add_exception_handler(FastAPIUsersException, fastapi_users_auth_exception_handler)
```

### 3.2. Worker Service Architecture

#### Entry point (`tracecat/dsl/worker.py`)
```python
async def main():
    # 1. Tạo Temporal client
    client = await get_temporal_client()

    # 2. Load activities
    activities = [
        *DSLActivities,                           # Core DSL activities
        *WorkflowSchedulesService.activities,     # Schedule activities
        *WorkflowsManagementService.activities,   # Management activities
        *InteractionService.activities,           # Interaction (EE)
        *AgentActivities,                         # AI agent (EE)
        *ApprovalManager.activities,              # Approvals (EE)
    ]

    # 3. Create worker
    worker = Worker(
        client,
        task_queue=config.TEMPORAL__CLUSTER_QUEUE,
        workflows=[DSLWorkflow, DurableAgentWorkflow],  # Temporal workflows
        activities=activities,
        activity_executor=ThreadPoolExecutor(max_workers=100),
        workflow_runner=new_sandbox_runner(),  # Sandboxed execution
    )

    # 4. Run worker
    await worker.run()
```

#### DSL Workflow (`tracecat/dsl/workflow.py`)

**Workflow definition**:
```python
@workflow.defn
class DSLWorkflow:
    """Temporal workflow chạy Tracecat DSL workflows."""

    @workflow.init
    def __init__(self, args: DSLRunArgs):
        # Setup context
        self.role = args.role
        self.wf_exec_id = workflow.info().workflow_id
        self.logger = logger.bind(wf_id=args.wf_id, ...)

        # Set context vars
        ctx_role.set(self.role)
        ctx_logger.set(self.logger)

    @workflow.run
    async def run(self, args: DSLRunArgs) -> dict[str, Any]:
        # 1. Load workflow definition
        defn = await self._get_workflow_definition(...)

        # 2. Parse DSL
        dsl = DSLConfig.from_dict(defn.content)

        # 3. Validate trigger inputs
        trigger_inputs = await self._validate_trigger_inputs(...)

        # 4. Execute workflow
        result = await self._run(dsl, trigger_inputs)

        return result
```

**Core execution loop**:
```python
async def _run(self, dsl: DSLConfig, trigger_inputs: TriggerInputs):
    # 1. Tạo scheduler (topological sort actions theo dependencies)
    scheduler = DSLScheduler(dsl)

    # 2. Initialize context
    context = ExecutionContext(
        workspace_id=self.role.workspace_id,
        trigger_inputs=trigger_inputs,
        env=dsl.environment,
    )

    # 3. Execute actions theo thứ tự
    for action_batch in scheduler.schedule():
        # Parallel execution trong batch
        tasks = [
            self._execute_action(action, context)
            for action in action_batch
        ]
        results = await asyncio.gather(*tasks)

        # Update context với results
        for action, result in zip(action_batch, results):
            context.add_result(action.ref, result)

    return context.results
```

### 3.3. Executor Service Architecture

#### Initialization (`tracecat/api/executor.py`)
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Initialize Ray cluster
    init_ray_cluster()

    yield

    # 2. Cleanup Ray
    shutdown_ray_cluster()

def init_ray_cluster():
    """Khởi tạo Ray cluster cho distributed computing."""
    ray.init(
        namespace="tracecat",
        num_cpus=min(os.cpu_count(), 8),
        dashboard_host="0.0.0.0",
        dashboard_port=8265,
        _system_config={
            "max_io_workers": 4,
        },
    )
```

#### Action execution (`tracecat/executor/service.py`)
```python
async def execute_action(action_input: RunActionInput) -> TaskResult:
    """Thực thi một action."""

    # 1. Evaluate templated args (resolve ${{ }} expressions)
    evaluated_args = await evaluate_templated_args(
        action_input.args,
        context=action_input.context,
    )

    # 2. Get action from registry
    action_defn = await get_action_definition(action_input.action)

    # 3. Execute based on action type
    if action_defn.action_type == "http_request":
        result = await execute_http_request(evaluated_args)
    elif action_defn.action_type == "python_script":
        result = await execute_python_script(evaluated_args)
    elif action_defn.action_type == "integration":
        result = await execute_integration(action_defn, evaluated_args)

    # 4. Return result
    return TaskResult(
        output=result,
        status="success",
        action_ref=action_input.ref,
    )
```

#### Expression evaluation (`tracecat/expressions/eval.py`)
Hệ thống đánh giá template expressions `${{ }}`:

```python
def eval_templated_object(obj: Any, context: ExprContext) -> Any:
    """
    Evaluate templated object.

    Examples:
        ${{ INPUTS.user_id }}           -> context.INPUTS["user_id"]
        ${{ SECRETS.api.TOKEN }}        -> decrypt(secrets["api"]["TOKEN"])
        ${{ FN.to_base64("hello") }}    -> "aGVsbG8="
        ${{ steps.fetch_data.result }}  -> context.steps["fetch_data"]["result"]
    """

    if isinstance(obj, str):
        # Parse và evaluate expression
        return eval_string_template(obj, context)

    elif isinstance(obj, dict):
        # Recursively evaluate dict values
        return {k: eval_templated_object(v, context) for k, v in obj.items()}

    elif isinstance(obj, list):
        # Recursively evaluate list items
        return [eval_templated_object(item, context) for item in obj]

    else:
        return obj
```

### 3.4. Frontend Architecture

#### Next.js 15 App Router structure
```
frontend/src/app/
├── (auth)/                   # Auth group
│   ├── sign-in/              # Login page
│   ├── sign-up/              # Register page
│   └── auth/                 # OAuth callbacks
│
├── workspaces/               # Main app
│   └── [workspaceId]/        # Dynamic workspace route
│       ├── workflows/        # Workflow builder
│       │   ├── [workflowId]/ # Workflow detail
│       │   │   └── page.tsx  # ReactFlow canvas
│       │   └── page.tsx      # Workflow list
│       │
│       ├── cases/            # Case management
│       │   ├── [caseId]/     # Case detail
│       │   └── page.tsx      # Case list
│       │
│       ├── agents/           # AI agents
│       ├── integrations/     # Integration config
│       ├── credentials/      # Secret management
│       ├── tables/           # Lookup tables
│       └── variables/        # Variables
│
├── registry/                 # Action registry browser
└── organization/             # Org settings
```

#### Component architecture
```
frontend/src/components/
├── ui/                       # shadcn/ui base components
│   ├── button.tsx
│   ├── dialog.tsx
│   ├── form.tsx
│   └── ...
│
├── workflow/                 # Workflow-specific
│   ├── canvas/               # ReactFlow canvas
│   ├── action-panel.tsx      # Action configuration
│   ├── node-types.tsx        # Custom node types
│   └── edge-types.tsx        # Custom edge types
│
├── cases/                    # Case management
│   ├── case-panel.tsx        # Case detail
│   ├── case-timeline.tsx     # Event timeline
│   └── case-form.tsx         # Create/edit form
│
├── agents/                   # AI agent components
│   ├── chat-interface.tsx    # Chat UI
│   └── agent-config.tsx      # Agent configuration
│
└── registry/                 # Registry browser
    └── action-card.tsx       # Action display
```

#### API Client generation
```bash
# Generate TypeScript client từ OpenAPI spec
just gen-client

# Output: frontend/src/client/
# - schemas.gen.ts       (Zod schemas)
# - types.gen.ts         (TypeScript types)
# - services.gen.ts      (API functions)
```

#### State management với React Query
```typescript
// Example: Fetch workflow
import { useQuery } from "@tanstack/react-query"
import { WorkflowsService } from "@/client"

function useWorkflow(workflowId: string) {
  return useQuery({
    queryKey: ["workflow", workflowId],
    queryFn: () => WorkflowsService.getWorkflow({ workflowId }),
  })
}

// Example: Update workflow
import { useMutation, useQueryClient } from "@tanstack/react-query"

function useUpdateWorkflow() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: WorkflowsService.updateWorkflow,
    onSuccess: (data) => {
      // Invalidate cache
      queryClient.invalidateQueries({
        queryKey: ["workflow", data.id],
      })
    },
  })
}
```

---

## 4. Luồng xử lý dữ liệu

### 4.1. Luồng tạo và chạy Workflow

#### Bước 1: Tạo workflow (UI → API)
```
User (Frontend)
    │
    ├─→ POST /workflows
    │       {
    │         title: "My Workflow",
    │         description: "...",
    │       }
    │
API Service (workflow_management_router)
    │
    ├─→ WorkflowsManagementService.create_workflow()
    │       ├─→ Create Workflow record
    │       ├─→ Create WorkflowDefinition record
    │       └─→ Commit to PostgreSQL
    │
    └─→ Return Workflow
```

#### Bước 2: Cấu hình actions (UI → API)
```
User adds actions in workflow builder
    │
    ├─→ PUT /workflows/{id}/definition
    │       {
    │         "actions": [
    │           {
    │             "ref": "fetch_data",
    │             "action": "core.http_request",
    │             "args": {
    │               "url": "https://api.example.com/data",
    │               "method": "GET"
    │             }
    │           },
    │           {
    │             "ref": "process_data",
    │             "action": "core.script.run_python",
    │             "args": {
    │               "script": "def main(data): return data['items']",
    │               "inputs": "${{ steps.fetch_data.result }}"
    │             },
    │             "depends_on": ["fetch_data"]
    │           }
    │         ]
    │       }
    │
API Service
    │
    ├─→ Validate DSL structure
    ├─→ Update WorkflowDefinition.content
    └─→ Save to database
```

#### Bước 3: Chạy workflow (Trigger → Worker → Executor)
```
Trigger (Webhook/Schedule/Manual)
    │
    ├─→ POST /webhooks/{path}  hoặc
    │   POST /workflows/{id}/executions
    │       { "inputs": { "user_id": "123" } }
    │
API Service (workflow_executions_router)
    │
    ├─→ Tạo WorkflowExecution record
    │
    ├─→ Start Temporal workflow
    │       client.start_workflow(
    │         DSLWorkflow,
    │         args=DSLRunArgs(...),
    │         id=execution_id,
    │         task_queue="tracecat-task-queue"
    │       )
    │
    └─→ Return execution_id

Temporal Server
    │
    └─→ Schedule workflow on task queue

Worker Service (DSLWorkflow)
    │
    ├─→ Poll Temporal task queue
    │
    ├─→ Execute workflow.run()
    │   │
    │   ├─→ Load workflow definition from DB
    │   │       (via get_workflow_definition_activity)
    │   │
    │   ├─→ Parse DSL
    │   │       dsl = DSLConfig.from_dict(defn.content)
    │   │
    │   ├─→ Create scheduler
    │   │       scheduler = DSLScheduler(dsl)
    │   │       # Topological sort theo depends_on
    │   │
    │   └─→ Execute actions
    │       │
    │       ├─→ Batch 1: ["fetch_data"]
    │       │   │
    │       │   └─→ Execute activity: run_action_activity
    │       │       │
    │       │       ├─→ Call Executor Service
    │       │       │       POST http://executor:8000/execute
    │       │       │       {
    │       │       │         "action": "core.http_request",
    │       │       │         "args": {...},
    │       │       │         "context": {...}
    │       │       │       }
    │       │       │
    │       │       └─→ Return result
    │       │               { "data": [...] }
    │       │
    │       └─→ Batch 2: ["process_data"]
    │           │
    │           └─→ Execute activity: run_action_activity
    │               │
    │               ├─→ Evaluate templated args
    │               │       "${{ steps.fetch_data.result }}"
    │               │       → { "data": [...] }
    │               │
    │               ├─→ Call Executor Service
    │               │       POST http://executor:8000/execute
    │               │       {
    │               │         "action": "core.script.run_python",
    │               │         "args": {
    │               │           "script": "...",
    │               │           "inputs": { "data": [...] }
    │               │         }
    │               │       }
    │               │
    │               └─→ Return result
    │
    └─→ Complete workflow
        │
        └─→ Temporal marks workflow as completed

Executor Service
    │
    ├─→ Receive action execution request
    │
    ├─→ Evaluate expressions trong args
    │       evaluate_templated_args()
    │       - Resolve ${{ INPUTS.* }}
    │       - Resolve ${{ SECRETS.* }}
    │       - Resolve ${{ FN.* }}
    │       - Resolve ${{ steps.*.result }}
    │
    ├─→ Execute action
    │   │
    │   ├─→ http_request: httpx.request(...)
    │   ├─→ python_script: exec(script, globals={"main": ...})
    │   └─→ integration: call integration function
    │
    └─→ Return result
```

### 4.2. Luồng xử lý Webhook

```
External System
    │
    ├─→ POST https://app.tracecat.com/webhooks/my-webhook-path
    │       Headers:
    │         X-Tracecat-Signature: sha256=...
    │       Body:
    │         { "alert": { "id": "123", "severity": "high" } }
    │
Caddy Reverse Proxy
    │
    └─→ Forward to API Service

API Service (webhook_router)
    │
    ├─→ Verify signature
    │       verify_webhook_signature(
    │         payload=request.body,
    │         signature=request.headers["X-Tracecat-Signature"],
    │         secret=webhook.secret
    │       )
    │
    ├─→ Find webhook by path
    │       webhook = get_webhook(path="my-webhook-path")
    │
    ├─→ Find workflow
    │       workflow = get_workflow(webhook.workflow_id)
    │
    ├─→ Start workflow execution
    │       trigger_inputs = TriggerInputs(webhook=request.json())
    │
    │       execution = start_workflow_execution(
    │         workflow_id=workflow.id,
    │         trigger_inputs=trigger_inputs,
    │         trigger_type="webhook"
    │       )
    │
    └─→ Return 200 OK
            { "execution_id": "exec_..." }

(Tiếp tục như luồng "Chạy workflow" ở trên)
```

### 4.3. Luồng xử lý Schedule (Cron)

```
Worker Service (WorkflowSchedulesService)
    │
    ├─→ Poll schedules activity
    │       schedules = list_active_schedules()
    │
    └─→ For each schedule:
        │
        ├─→ Check if should run (croniter)
        │       if schedule.should_run():
        │
        ├─→ Start workflow execution
        │       trigger_inputs = TriggerInputs(schedule={...})
        │
        │       execution = start_workflow_execution(
        │         workflow_id=schedule.workflow_id,
        │         trigger_inputs=trigger_inputs,
        │         trigger_type="schedule"
        │       )
        │
        └─→ Update schedule.last_run_at
```

### 4.4. Luồng Case Management

```
User creates case (Frontend)
    │
    ├─→ POST /cases
    │       {
    │         "title": "Phishing Email Detected",
    │         "priority": "high",
    │         "severity": "medium",
    │         "status": "open",
    │         "fields": {
    │           "affected_user": "user@company.com",
    │           "email_subject": "Urgent: Reset Password"
    │         }
    │       }
    │
API Service (cases_router)
    │
    ├─→ CaseService.create_case()
    │   │
    │   ├─→ Create Case record
    │   │       case = Case(
    │   │         title="...",
    │   │         priority="high",
    │   │         workspace_id=workspace_id
    │   │       )
    │   │
    │   ├─→ Create CaseFields record
    │   │       fields = CaseFields(
    │   │         case_id=case.id,
    │   │         fields={"affected_user": "...", ...}
    │   │       )
    │   │
    │   ├─→ Create CaseEvent record (case created)
    │   │       event = CaseEvent(
    │   │         case_id=case.id,
    │   │         type="case_created",
    │   │         data={...}
    │   │       )
    │   │
    │   └─→ Commit to database
    │
    └─→ Return Case

User adds comment
    │
    ├─→ POST /cases/{id}/comments
    │       { "text": "Investigating email headers..." }
    │
API Service
    │
    ├─→ Create CaseComment record
    ├─→ Create CaseEvent record (comment added)
    └─→ Return Comment

User uploads attachment
    │
    ├─→ POST /cases/{id}/attachments
    │       multipart/form-data: file=email.eml
    │
API Service (case_attachments_router)
    │
    ├─→ Upload file to MinIO
    │       blob_storage.upload(
    │         bucket="case-attachments",
    │         key=f"{case_id}/{filename}",
    │         file=request.file
    │       )
    │
    ├─→ Create attachment record
    │       attachment = Attachment(
    │         case_id=case_id,
    │         filename="email.eml",
    │         storage_key=f"{case_id}/email.eml",
    │         size=file.size
    │       )
    │
    └─→ Generate presigned URL
            presigned_url = blob_storage.get_presigned_url(
              key=attachment.storage_key,
              expires_in=3600  # 1 hour
            )
```

---

## 5. Database Schema

### 5.1. Core Tables

#### **User** (Authentication)
```sql
CREATE TABLE users (
    id UUID PRIMARY KEY,
    email VARCHAR UNIQUE NOT NULL,
    hashed_password VARCHAR,
    is_active BOOLEAN DEFAULT true,
    is_superuser BOOLEAN DEFAULT false,
    is_verified BOOLEAN DEFAULT false,
    organization_id UUID REFERENCES organizations(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

#### **Workspace** (Multi-tenancy)
```sql
CREATE TABLE workspaces (
    id UUID PRIMARY KEY,
    name VARCHAR NOT NULL,
    organization_id UUID REFERENCES organizations(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE memberships (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    workspace_id UUID REFERENCES workspaces(id),
    role VARCHAR NOT NULL,  -- viewer, editor, admin, owner
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, workspace_id)
);
```

#### **Workflow** (Workflows)
```sql
CREATE TABLE workflows (
    id UUID PRIMARY KEY,
    title VARCHAR NOT NULL,
    description TEXT,
    status VARCHAR DEFAULT 'offline',  -- online, offline
    workspace_id UUID REFERENCES workspaces(id),
    owner_id UUID REFERENCES users(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE workflow_definitions (
    id UUID PRIMARY KEY,
    workflow_id UUID REFERENCES workflows(id) UNIQUE,
    version INT DEFAULT 1,
    content JSONB NOT NULL,  -- DSL definition
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

**DSL Definition format** (trong `content` JSONB):
```json
{
  "title": "Investigate Phishing Email",
  "description": "Workflow to analyze phishing emails",
  "entrypoint": "extract_indicators",
  "actions": [
    {
      "ref": "extract_indicators",
      "action": "tools.email.extract_indicators",
      "args": {
        "email_content": "${{ INPUTS.email }}"
      }
    },
    {
      "ref": "check_reputation",
      "action": "tools.virustotal.check_ip",
      "args": {
        "ip_addresses": "${{ steps.extract_indicators.result.ips }}"
      },
      "depends_on": ["extract_indicators"]
    },
    {
      "ref": "create_case",
      "action": "core.case.create",
      "args": {
        "title": "Phishing: ${{ INPUTS.subject }}",
        "priority": "high",
        "fields": {
          "indicators": "${{ steps.extract_indicators.result }}",
          "reputation": "${{ steps.check_reputation.result }}"
        }
      },
      "depends_on": ["check_reputation"]
    }
  ],
  "triggers": [
    {
      "type": "webhook",
      "ref": "email_webhook"
    }
  ],
  "config": {
    "enable_runtime_tests": false
  }
}
```

#### **Action** (Workflow actions)
```sql
-- Actions được lưu trong workflow_definitions.content (JSONB)
-- Không có table riêng, chúng là part của DSL
```

#### **Webhook** (Triggers)
```sql
CREATE TABLE webhooks (
    id UUID PRIMARY KEY,
    workflow_id UUID REFERENCES workflows(id),
    path VARCHAR UNIQUE NOT NULL,  -- /webhooks/{path}
    method VARCHAR DEFAULT 'POST',  -- GET, POST, PUT
    status VARCHAR DEFAULT 'online',
    workspace_id UUID REFERENCES workspaces(id),
    owner_id UUID REFERENCES users(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE webhook_api_keys (
    id UUID PRIMARY KEY,
    webhook_id UUID REFERENCES webhooks(id),
    key_hash VARCHAR NOT NULL,
    name VARCHAR,
    created_at TIMESTAMP DEFAULT NOW()
);
```

#### **Schedule** (Cron triggers)
```sql
CREATE TABLE schedules (
    id UUID PRIMARY KEY,
    workflow_id UUID REFERENCES workflows(id),
    cron_expression VARCHAR NOT NULL,  -- "0 9 * * MON-FRI"
    enabled BOOLEAN DEFAULT true,
    last_run_at TIMESTAMP,
    next_run_at TIMESTAMP,
    workspace_id UUID REFERENCES workspaces(id),
    owner_id UUID REFERENCES users(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

### 5.2. Secret Management

```sql
CREATE TABLE secrets (
    id UUID PRIMARY KEY,
    name VARCHAR NOT NULL,
    type VARCHAR NOT NULL,  -- custom, oauth2, token, api_key
    encrypted_keys BYTEA NOT NULL,  -- Fernet encrypted
    workspace_id UUID REFERENCES workspaces(id),
    owner_id UUID REFERENCES users(id),
    environment VARCHAR DEFAULT 'default',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(name, workspace_id, environment)
);
```

**Encrypted format**:
```python
# Before encryption (plaintext)
{
    "API_KEY": "sk-1234567890abcdef",
    "API_SECRET": "secret-value"
}

# After encryption (stored in encrypted_keys)
# Encrypted với Fernet (TRACECAT__DB_ENCRYPTION_KEY)
b'gAAAAABh...'  # Fernet token
```

**Usage trong workflow**:
```yaml
- ref: call_api
  action: core.http_request
  args:
    url: https://api.example.com/data
    headers:
      Authorization: "Bearer ${{ SECRETS.my_api.API_KEY }}"
```

### 5.3. Case Management

```sql
CREATE TABLE cases (
    id UUID PRIMARY KEY,
    title VARCHAR NOT NULL,
    priority VARCHAR NOT NULL,     -- low, medium, high, critical
    severity VARCHAR NOT NULL,     -- info, low, medium, high, critical
    status VARCHAR NOT NULL,       -- open, in_progress, closed, resolved
    workspace_id UUID REFERENCES workspaces(id),
    owner_id UUID REFERENCES users(id),
    assignee_id UUID REFERENCES users(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE case_fields (
    id UUID PRIMARY KEY,
    case_id UUID REFERENCES cases(id) UNIQUE,
    fields JSONB NOT NULL,  -- Custom fields
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE case_comments (
    id UUID PRIMARY KEY,
    case_id UUID REFERENCES cases(id),
    user_id UUID REFERENCES users(id),
    text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE case_events (
    id UUID PRIMARY KEY,
    case_id UUID REFERENCES cases(id),
    type VARCHAR NOT NULL,  -- case_created, status_changed, comment_added
    data JSONB,
    user_id UUID REFERENCES users(id),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE case_tags (
    id UUID PRIMARY KEY,
    case_id UUID REFERENCES cases(id),
    tag_id UUID REFERENCES tag_definitions(id),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(case_id, tag_id)
);

CREATE TABLE case_tag_definitions (
    id UUID PRIMARY KEY,
    name VARCHAR NOT NULL,
    color VARCHAR,  -- hex color
    workspace_id UUID REFERENCES workspaces(id),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(name, workspace_id)
);
```

### 5.4. Integration & Registry

```sql
CREATE TABLE registry_repositories (
    id UUID PRIMARY KEY,
    origin VARCHAR UNIQUE NOT NULL,  -- local, remote, git
    uri VARCHAR,                     -- Git URL hoặc local path
    enabled BOOLEAN DEFAULT true,
    workspace_id UUID REFERENCES workspaces(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE registry_actions (
    id UUID PRIMARY KEY,
    name VARCHAR NOT NULL,           -- "tools.virustotal.check_ip"
    repository_id UUID REFERENCES registry_repositories(id),
    namespace VARCHAR,                -- "tools.virustotal"
    version VARCHAR DEFAULT '0.1.0',
    definition JSONB NOT NULL,       -- Action template definition
    workspace_id UUID REFERENCES workspaces(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(name, version, workspace_id)
);

-- OAuth integrations
CREATE TABLE integrations (
    id UUID PRIMARY KEY,
    provider VARCHAR NOT NULL,       -- google, github, okta, etc.
    status VARCHAR DEFAULT 'pending',-- pending, active, error
    access_token_enc BYTEA,          -- Encrypted access token
    refresh_token_enc BYTEA,         -- Encrypted refresh token
    expires_at TIMESTAMP,
    workspace_id UUID REFERENCES workspaces(id),
    owner_id UUID REFERENCES users(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

### 5.5. Tables (Lookup Tables)

```sql
CREATE TABLE tables (
    id UUID PRIMARY KEY,
    name VARCHAR NOT NULL,
    description TEXT,
    workspace_id UUID REFERENCES workspaces(id),
    owner_id UUID REFERENCES users(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(name, workspace_id)
);

CREATE TABLE table_columns (
    id UUID PRIMARY KEY,
    table_id UUID REFERENCES tables(id),
    name VARCHAR NOT NULL,
    type VARCHAR NOT NULL,  -- string, number, boolean, json
    created_at TIMESTAMP DEFAULT NOW()
);

-- Table data stored in MinIO as Parquet files
-- Path: workspace_id/tables/table_id/data.parquet
```

**Usage trong workflow**:
```yaml
- ref: check_allowlist
  action: core.table.lookup
  args:
    table_name: "ip_allowlist"
    key: "${{ steps.extract_ip.result.ip }}"
    column: "ip_address"
```

### 5.6. Migration System (Alembic)

```bash
alembic/versions/
├── 001_initial_schema.py
├── 002_add_workspaces.py
├── 003_add_cases.py
├── 004_add_integrations.py
└── ...

# Run migrations
export TRACECAT__DB_URI=postgresql+psycopg://...
alembic upgrade head

# Create new migration
alembic revision --autogenerate -m "Add new feature"
```

---

## 6. Hệ thống Workflow và DSL

### 6.1. DSL (Domain Specific Language)

Tracecat sử dụng một DSL được định nghĩa trong YAML/JSON để mô tả workflows.

#### Cấu trúc DSL
```yaml
title: "Investigate Suspicious Email"
description: "Automated phishing email investigation"
entrypoint: "parse_email"  # Action đầu tiên

config:
  enable_runtime_tests: false
  scheduler: "static"  # static hoặc dynamic

environment:
  variables:
    - name: "MAX_RETRIES"
      value: 3

actions:
  - ref: "parse_email"              # Unique reference
    action: "tools.email.parse"     # Action từ registry
    args:
      email_content: "${{ INPUTS.email }}"

  - ref: "extract_indicators"
    action: "tools.ioc.extract"
    args:
      text: "${{ steps.parse_email.result.body }}"
    depends_on: ["parse_email"]    # Dependencies

  - ref: "check_virustotal"
    action: "tools.virustotal.check_ip"
    args:
      ip_addresses: "${{ steps.extract_indicators.result.ips }}"
    depends_on: ["extract_indicators"]
    run_if: "${{ FN.len(steps.extract_indicators.result.ips) > 0 }}"  # Conditional

  - ref: "parallel_checks"
    action: "core.workflow.loop"
    args:
      for_each: "${{ steps.extract_indicators.result.ips }}"
      actions:
        - ref: "check_ip"
          action: "tools.virustotal.check_ip"
          args:
            ip: "${{ item }}"
    loop_strategy: "parallel"       # parallel hoặc batch

  - ref: "create_case"
    action: "core.case.create"
    args:
      title: "Phishing: ${{ steps.parse_email.result.subject }}"
      priority: "${{ steps.check_virustotal.result.risk_level }}"
      fields:
        indicators: "${{ steps.extract_indicators.result }}"
        vt_results: "${{ steps.check_virustotal.result }}"
    depends_on: ["check_virustotal"]

triggers:
  - type: "webhook"
    ref: "email_webhook"
    path: "/email-alert"

  - type: "schedule"
    ref: "daily_scan"
    cron: "0 9 * * *"
```

### 6.2. Expression System

#### Template syntax: `${{ }}`

**1. Access inputs**:
```yaml
${{ INPUTS.user_id }}              # Trigger input
${{ INPUTS.webhook.body.alert }}   # Nested webhook data
```

**2. Access secrets**:
```yaml
${{ SECRETS.api_key.API_KEY }}     # Secret value
${{ SECRETS.github.TOKEN }}        # OAuth token
```

**3. Access previous action results**:
```yaml
${{ steps.fetch_data.result }}                  # Full result
${{ steps.fetch_data.result.items[0].id }}      # Nested access
${{ steps.fetch_data.result.items | length }}   # Filter
```

**4. Functions** (`FN.*`):

Xem `/tracecat/expressions/functions.py:905` (`_FUNCTION_MAPPING`) cho danh sách đầy đủ:

```yaml
# String functions
${{ FN.to_upper("hello") }}                     # "HELLO"
${{ FN.to_lower("WORLD") }}                     # "world"
${{ FN.concat("Hello", " ", "World") }}         # "Hello World"
${{ FN.format("{} - {}", "Item", 123) }}        # "Item - 123"

# Encoding
${{ FN.to_base64("hello") }}                    # "aGVsbG8="
${{ FN.from_base64("aGVsbG8=") }}                # "hello"
${{ FN.url_encode("hello world") }}             # "hello%20world"

# JSON
${{ FN.to_json({"key": "value"}) }}             # '{"key": "value"}'
${{ FN.from_json('{"key": "value"}') }}         # {"key": "value"}

# Collections
${{ FN.len([1, 2, 3]) }}                        # 3
${{ FN.keys({"a": 1, "b": 2}) }}                # ["a", "b"]
${{ FN.values({"a": 1, "b": 2}) }}              # [1, 2]

# Logic
${{ FN.if(condition, true_value, false_value) }}
${{ FN.is_null(value) }}
${{ FN.is_empty(list) }}

# Time
${{ FN.now() }}                                 # Current timestamp
${{ FN.format_time(timestamp, "YYYY-MM-DD") }}
```

**5. Operators**:
```yaml
# Logical (NOTE: Sử dụng || và &&, KHÔNG phải or/and)
${{ inputs.login || inputs.email }}              # OR
${{ inputs.enabled && inputs.verified }}         # AND

# Comparison
${{ inputs.age > 18 }}
${{ inputs.status == "active" }}
${{ inputs.count >= 10 }}

# String concatenation
${{ "Hello " + inputs.name }}
```

### 6.3. Scheduler

DSL Scheduler thực hiện **topological sort** actions theo `depends_on` relationships.

```python
# tracecat/dsl/scheduler.py

class DSLScheduler:
    def __init__(self, dsl: DSLConfig):
        self.actions = dsl.actions
        self.graph = self._build_dependency_graph()

    def schedule(self) -> list[list[ActionStatement]]:
        """
        Trả về list of batches.
        Mỗi batch chứa actions có thể chạy song song.
        """

        # Topological sort using Kahn's algorithm
        in_degree = {action.ref: 0 for action in self.actions}

        for action in self.actions:
            for dep in action.depends_on:
                in_degree[action.ref] += 1

        batches = []
        ready = [a for a in self.actions if in_degree[a.ref] == 0]

        while ready:
            batches.append(ready)
            next_ready = []

            for action in ready:
                for dependent in self.graph[action.ref]:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        next_ready.append(dependent)

            ready = next_ready

        return batches

# Example:
# Actions: A, B, C, D
# Dependencies:
#   A: []
#   B: [A]
#   C: [A]
#   D: [B, C]
#
# Schedule result:
# [
#   [A],        # Batch 1: A chạy trước
#   [B, C],     # Batch 2: B và C chạy song song (cùng depend on A)
#   [D]         # Batch 3: D chạy sau (depend on B và C)
# ]
```

### 6.4. Execution Context

```python
# tracecat/dsl/schemas.py

@dataclass
class ExecutionContext:
    """
    Context được truyền qua tất cả actions trong workflow.
    """

    workspace_id: str
    trigger_inputs: TriggerInputs  # INPUTS.*
    env: DSLEnvironment            # ENV vars

    # Results từ các actions trước đó
    results: dict[str, TaskResult] = field(default_factory=dict)

    # Secrets cache (decrypted)
    secrets: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add_result(self, ref: str, result: TaskResult):
        """Thêm result từ action vào context."""
        self.results[ref] = result

    def get_result(self, ref: str) -> TaskResult:
        """Lấy result từ action trước đó."""
        return self.results[ref]

    def to_expr_context(self) -> ExprContext:
        """Convert sang ExprContext để evaluate expressions."""
        return ExprContext(
            INPUTS=self.trigger_inputs.model_dump(),
            SECRETS=self.secrets,
            steps={
                ref: {"result": result.output}
                for ref, result in self.results.items()
            },
            ENV=self.env.variables,
        )
```

### 6.5. Retry và Error Handling

```python
# tracecat/dsl/common.py

RETRY_POLICIES = {
    "default": RetryPolicy(
        initial_interval=timedelta(seconds=1),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(seconds=60),
        maximum_attempts=3,
    ),
    "aggressive": RetryPolicy(
        initial_interval=timedelta(milliseconds=100),
        backoff_coefficient=1.5,
        maximum_interval=timedelta(seconds=10),
        maximum_attempts=5,
    ),
    "conservative": RetryPolicy(
        initial_interval=timedelta(seconds=5),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(minutes=5),
        maximum_attempts=2,
    ),
}
```

**Trong DSL**:
```yaml
- ref: "flaky_api_call"
  action: "core.http_request"
  args:
    url: "https://api.example.com/data"
  retry_policy: "aggressive"

  # Fail strategy
  fail_strategy: "isolated"  # hoặc "all"
  # isolated: Lỗi không làm fail toàn bộ workflow
  # all: Lỗi làm fail toàn bộ workflow
```

---

(Tiếp tục phần 7-9 trong file tiếp theo...)
