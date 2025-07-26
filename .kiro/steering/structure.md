# Project Structure

## Root Level Organization

```
tracecat/
├── tracecat/           # Core Python application
├── frontend/           # Next.js web application
├── registry/           # Action templates and integrations
├── tests/              # Test suites
├── docs/               # Documentation (Mintlify)
├── alembic/            # Database migrations
└── scripts/            # Utility scripts
```

## Core Application (`tracecat/`)

The main Python application follows a modular architecture:

- **`api/`** - FastAPI application and routing
- **`auth/`** - Authentication, authorization, and user management
- **`cases/`** - Case management functionality
- **`dsl/`** - Domain Specific Language for workflows
- **`executor/`** - Action execution engine
- **`expressions/`** - Expression evaluation and functions
- **`integrations/`** - Integration management
- **`registry/`** - Action registry and repository management
- **`secrets/`** - Secrets management and encryption
- **`workflow/`** - Workflow definition and management
- **`db/`** - Database models and utilities

## Frontend (`frontend/`)

Next.js application with App Router:

- **`src/app/`** - App Router pages and layouts
- **`src/components/`** - Reusable React components
- **`src/lib/`** - Utility functions and configurations
- **`src/client/`** - Generated API client
- **`src/hooks/`** - Custom React hooks
- **`src/types/`** - TypeScript type definitions

## Registry (`registry/`)

Separate Python package for action templates:

- **`tracecat_registry/templates/`** - YAML action templates organized by provider
- **`tracecat_registry/integrations/`** - Python integration implementations
- **`tracecat_registry/core/`** - Core registry functionality

## Testing Structure (`tests/`)

- **`unit/`** - Unit tests for individual components
- **`registry/`** - Registry-specific tests
- **`data/`** - Test fixtures and sample data

## Key Conventions

### Python Code Organization

- Each module has clear separation of concerns (models, services, routers)
- Database models use SQLModel for Pydantic integration
- Services contain business logic, routers handle HTTP concerns
- Dependency injection pattern for database sessions and authentication

### Frontend Code Organization

- Components organized by feature/domain
- Shared components in `components/ui/` following shadcn/ui patterns
- Custom hooks for API interactions and state management
- Type-safe API client generated from OpenAPI spec

### Configuration Management

- Environment variables prefixed with `TRACECAT__`
- Separate configs for development (`docker-compose.dev.yml`) and production
- Settings validation using Pydantic models

### Database Patterns

- Alembic migrations in `alembic/versions/`
- UUID primary keys for most entities
- Soft deletes and audit fields where appropriate
- Multi-tenant architecture with organization-based isolation

### Action Template Structure

Registry templates follow consistent YAML schema:

```yaml
name: action_name
description: Human readable description
namespace: provider.category
expects:
  # Input schema
returns:
  # Output schema
```

## Import Patterns

- Relative imports within modules
- Absolute imports from `tracecat.*` for cross-module dependencies
- Frontend uses path aliases configured in `tsconfig.json`
