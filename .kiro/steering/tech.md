# Technology Stack

## Backend

- **Python 3.12+** - Core application language
- **FastAPI** - Web framework with automatic OpenAPI generation
- **SQLModel/SQLAlchemy** - Database ORM with Pydantic integration
- **PostgreSQL** - Primary database for application data
- **Alembic** - Database migration management
- **Temporal** - Workflow orchestration engine
- **Ray** - Distributed computing for action execution
- **Pydantic** - Data validation and serialization

## Frontend

- **Next.js 15** - React framework with App Router
- **TypeScript** - Type-safe JavaScript
- **Tailwind CSS** - Utility-first CSS framework
- **Radix UI** - Accessible component primitives
- **React Query (TanStack)** - Server state management
- **React Flow** - Visual workflow editor
- **CodeMirror** - Code editor components

## Infrastructure & Deployment

- **Docker & Docker Compose** - Containerization
- **Caddy** - Reverse proxy and TLS termination
- **PostgreSQL 16** - Application database
- **PostgreSQL 13** - Temporal database
- **Temporal Server** - Workflow orchestration

## Development Tools

- **Ruff** - Python linting and formatting
- **Biome** - JavaScript/TypeScript linting and formatting
- **pytest** - Python testing framework
- **Jest** - JavaScript testing framework
- **MyPy** - Python static type checking
- **Just** - Command runner (alternative to Make)

## Common Commands

### Development

```bash
# Start development environment
just dev

# Run tests
just test
pytest tests/

# Linting and formatting
just lint-fix        # Fix both Python and frontend
ruff check . && ruff format .  # Python only
cd frontend && pnpm check     # Frontend only

# Build containers
just build-dev       # Development build
just build          # Production build
```

### Frontend Development

```bash
cd frontend
pnpm dev            # Start dev server
pnpm build          # Production build
pnpm generate-client # Generate API client from OpenAPI
pnpm test           # Run tests
```

### Database Management

```bash
# Run migrations (handled automatically in containers)
alembic upgrade head

# Create new migration
alembic revision --autogenerate -m "description"
```

### Registry Development

```bash
# Generate integrations and functions
just gen-integrations
just gen-functions
just gen-api
```

## Package Management

- **Python**: Uses `uv` for fast dependency resolution
- **Frontend**: Uses `pnpm` for efficient package management
- **Containers**: Multi-stage builds for optimized images
