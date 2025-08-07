# Custom Entities System

The custom entities system provides a flexible, schema-based approach for storing and querying structured data with dynamic field definitions. It allows users to define custom entity types with typed fields that are stored efficiently in PostgreSQL JSONB columns.

## Architecture Overview

The system implements a three-tier architecture:

1. **Schema Layer**: Entity and field metadata definitions
2. **Data Layer**: JSONB storage with type-safe validation
3. **Query Layer**: Optimized JSONB queries with GIN index support

### Key Features (v1)

- **Immutable Field Schemas**: Field keys and types cannot be changed after creation
- **Flat JSONB Structure**: No nested objects (v1 limitation)
- **Type Safety**: Full validation at write and query time
- **Nullable Fields**: All fields are optional/nullable
- **Soft Delete**: Fields can be deactivated without data loss
- **GIN Index Optimization**: Queries optimized for PostgreSQL GIN indexes

## Module Overview

### `service.py` - Service Layer

The main service layer providing business logic and orchestration:

- **Entity Management**: Create, read, update entity types
- **Field Management**: Define fields with types and validation rules
- **Data Operations**: CRUD operations on entity records
- **Validation**: Ensures data matches field definitions
- **Authorization**: Workspace-scoped with access control

Key class: `CustomEntitiesService` extends `BaseWorkspaceService`

Example:
```python
service = CustomEntitiesService(session, role)

# Create entity type
entity = await service.create_entity_type(
    name="customer",
    display_name="Customer",
    description="Customer records"
)

# Add fields
field = await service.create_field(
    entity_id=entity.id,
    field_key="email",
    field_type=FieldType.TEXT,
    display_name="Email Address",
    settings={"pattern": r"^[\w\.-]+@[\w\.-]+\.\w+$"}
)

# Create records
record = await service.create_record(
    entity_id=entity.id,
    data={"email": "user@example.com"}
)
```

### `query.py` - Query Builder

Type-safe JSONB query builder optimized for PostgreSQL:

- **Field Validation**: Validates fields exist before querying
- **Type Checking**: Ensures query values match field types
- **GIN Optimization**: Uses patterns that leverage GIN indexes
- **Operator Support**: `eq`, `in`, `ilike`, `contains`, `between`, `is_null`

Key class: `EntityQueryBuilder`

Example:
```python
builder = EntityQueryBuilder(session)

# Build filters
stmt = await builder.build_query(
    base_stmt,
    entity_id,
    filters=[
        {"field": "status", "operator": "eq", "value": "active"},
        {"field": "created_at", "operator": "between",
         "value": {"start": "2024-01-01", "end": "2024-12-31"}}
    ]
)
```

### `models.py` - Pydantic Models

API request/response models with validation:

- **Entity Models**: `EntityMetadataCreate`, `EntityMetadataRead`
- **Field Models**: `FieldMetadataCreate`, `FieldMetadataRead`
- **Data Models**: `EntityDataCreate`, `EntityDataRead`
- **Query Models**: `QueryRequest`, `QueryResponse`

Key validations:
- Field keys must be alphanumeric with underscores
- Must start with letter, be lowercase
- Cannot use reserved keywords
- Maximum 100 characters

### `types.py` - Type Definitions

Field type system and validation protocols:

- **Primitive Types**: `INTEGER`, `NUMBER`, `TEXT`, `BOOL`
- **Date/Time Types**: `DATE`, `DATETIME` (ISO format)
- **Array Types**: `ARRAY_TEXT`, `ARRAY_INTEGER`, `ARRAY_NUMBER`
- **Select Types**: `SELECT`, `MULTI_SELECT`

Key functions:
- `validate_flat_structure()`: Ensures no nested objects
- `get_python_type()`: Maps field types to Python types

### `common.py` - Utilities

Validation and serialization utilities:

- **Type Validation**: `validate_value_for_type()` checks values against field types
- **Settings Validation**: Enforces constraints (min/max, patterns, options)
- **Serialization**: `serialize_value()` prepares data for JSONB storage
- **Deserialization**: `deserialize_value()` reconstructs Python types

## Database Schema

The system uses three main tables:

1. **EntityMetadata**: Entity type definitions
   - `name`: Unique identifier (immutable)
   - `display_name`: Human-readable name
   - `settings`: Configuration JSONB

2. **FieldMetadata**: Field definitions for entities
   - `field_key`: Unique field identifier (immutable)
   - `field_type`: Data type (immutable)
   - `field_settings`: Validation constraints

3. **EntityData**: Actual entity records
   - `field_data`: JSONB column with field values
   - `entity_metadata_id`: Links to entity type

## JSONB Query Optimization

The query system has been optimized for PostgreSQL JSONB with GIN indexes, following SQLAlchemy 2.0 best practices.

### Key Improvements

#### 1. Proper JSONB Comparison Patterns

**Issue**: Direct comparison between JSONB and primitive types causes PostgreSQL errors
```python
# ❌ Before - Causes "operator does not exist: jsonb = integer" error
EntityData.field_data[field_key] == 25

# ✅ After - Uses astext for consistent comparison
EntityData.field_data[field_key].astext == "25"
```

#### 2. Native JSONB Methods

**Issue**: Manual operator usage was less readable and type-safe
```python
# ❌ Before - Manual operator
EntityData.field_data[field_key].op("@>")(values)

# ✅ After - Native contains() method
EntityData.field_data[field_key].contains(values)
```

#### 3. Proper Null Handling

**Issue**: JSON null vs missing key distinction
```python
# ✅ Correct approach
# JSON null becomes SQL NULL when using astext
EntityData.field_data[field_key].astext.is_(None)
```

#### 4. Boolean Value Handling

**Issue**: JSON booleans need special handling
```python
# ✅ JSON booleans are lowercase strings
EntityData.field_data[field_key].astext == ("true" if value else "false")
```

### Performance Optimizations

#### GIN Index Usage
All queries are optimized for PostgreSQL GIN indexes on JSONB columns:

1. **Top-level key access**: Uses `field_data[key]` pattern for GIN optimization
2. **Native operators**: Uses `?` for key existence, `@>` for containment via `.contains()`
3. **Efficient comparisons**: Uses `astext` for text extraction with index support

#### Query Patterns

| Operation | Pattern | GIN Optimized |
|-----------|---------|---------------|
| Equality | `field_data[key].astext = value` | ✅ |
| Contains | `field_data[key].contains(array)` | ✅ |
| Key exists | `field_data.op("?")(key)` | ✅ |
| ILIKE | `field_data[key].astext.ilike(pattern)` | ✅ |
| Range | `field_data[key].astext.cast(Numeric) BETWEEN` | ✅ |

### SQLAlchemy 2.0 Compliance

1. **Type Safety**: Proper type hints and casting where needed
   - Uses `ColumnElement[bool]` for query expressions
   - Explicit type parameters for generic types
   - No `# type: ignore` comments needed
2. **Native Methods**: Uses built-in JSONB comparator methods where available
3. **Error Handling**: Avoids PostgreSQL type mismatch errors
4. **Documentation**: Follows SQLAlchemy 2.0 PostgreSQL dialect documentation

## Usage Example

Complete workflow for creating and querying custom entities:

```python
# Initialize service
service = CustomEntitiesService(session, role)

# 1. Create entity type
customer_entity = await service.create_entity_type(
    name="customer",
    display_name="Customer",
    description="Customer records"
)

# 2. Define fields
await service.create_field(
    entity_id=customer_entity.id,
    field_key="name",
    field_type=FieldType.TEXT,
    display_name="Full Name"
)

await service.create_field(
    entity_id=customer_entity.id,
    field_key="age",
    field_type=FieldType.INTEGER,
    display_name="Age",
    settings={"min": 0, "max": 150}
)

await service.create_field(
    entity_id=customer_entity.id,
    field_key="tags",
    field_type=FieldType.ARRAY_TEXT,
    display_name="Tags"
)

# 3. Create records
record = await service.create_record(
    entity_id=customer_entity.id,
    data={
        "name": "John Doe",
        "age": 30,
        "tags": ["premium", "active"]
    }
)

# 4. Query records
results = await service.query_records(
    entity_id=customer_entity.id,
    filters=[
        {"field": "age", "operator": "between", "value": {"start": 25, "end": 35}},
        {"field": "tags", "operator": "contains", "value": ["premium"]}
    ]
)
```

## Design Decisions

### Why Immutable Field Schemas?

Fields cannot change their key or type after creation to ensure:
- Data consistency across all records
- Query performance (predictable types)
- Simplified migration path for schema changes

### Why Flat JSONB Structure?

v1 restricts to flat structures (no nested objects) for:
- Simplified validation logic
- Better query performance with GIN indexes
- Clear migration path to v2 with nested support

### Why Soft Delete?

Fields are deactivated rather than deleted to:
- Preserve existing data
- Allow reactivation if needed
- Maintain audit trail

## Supported Field Types

### Current Field Types (v1)

All field types are defined in `types.py` as an enum `FieldType`:

| Type | Python Type | Storage Format | Description |
|------|-------------|----------------|-------------|
| `INTEGER` | `int` | JSON number | Whole numbers (validates not bool) |
| `NUMBER` | `float` | JSON number | Decimal numbers |
| `TEXT` | `str` | JSON string | Text values |
| `BOOL` | `bool` | JSON boolean | True/False values |
| `DATE` | `date` | ISO string (YYYY-MM-DD) | Date values |
| `DATETIME` | `datetime` | ISO 8601 string | Date and time values |
| `ARRAY_TEXT` | `list[str]` | JSON array | Array of strings |
| `ARRAY_INTEGER` | `list[int]` | JSON array | Array of integers |
| `ARRAY_NUMBER` | `list[float]` | JSON array | Array of numbers |
| `SELECT` | `str` | JSON string | Single selection from options |
| `MULTI_SELECT` | `list[str]` | JSON array | Multiple selections from options |

### Field Type Settings

Each field type can have specific validation settings:

- **INTEGER/NUMBER**: `min`, `max` for range constraints
- **TEXT**: `min_length`, `max_length`, `pattern` (regex)
- **SELECT/MULTI_SELECT**: `options` list of allowed values
- **ARRAY_***: `max_items` for array size limit

### Adding New Field Types

To add a new field type:

1. **Add to FieldType enum** in `types.py`:
```python
class FieldType(StrEnum):
    # ... existing types ...
    URL = "URL"  # New type
```

2. **Update type mapping** in `types.py`:
```python
def get_python_type(field_type: FieldType, is_required: bool = False):
    type_map = {
        # ... existing mappings ...
        FieldType.URL: str,  # Map to Python type
    }
```

3. **Add validation logic** in `common.py`:
```python
def validate_value_for_type(value, field_type, settings):
    # ... existing validations ...
    elif field_type == FieldType.URL:
        if not isinstance(value, str):
            return False, "Expected string for URL"
        # Add URL-specific validation
        import re
        url_pattern = r'^https?://.*'
        if not re.match(url_pattern, value):
            return False, "Invalid URL format"
```

4. **Add serialization** if needed in `common.py`:
```python
def serialize_value(value, field_type):
    # Most types are already JSON-serializable
    # Add special handling if needed
```

5. **Update query builder** in `query.py` if special query operations are needed

## Relation Fields (Not Yet Implemented)

Based on the screenshot showing Twenty CRM's implementation, relation fields would typically support:

- **Has Many**: One entity can have multiple related entities (1:N relationship)
- **Belongs To One**: Entity belongs to exactly one parent entity (N:1 relationship)
- **Has One** (future): One-to-one relationship
- **Many to Many** (future): M:N relationship with junction table

### Current Status

**Relation fields are NOT implemented in v1**. The current implementation:
- Uses foreign keys only for metadata relationships (EntityData → EntityMetadata, FieldMetadata → EntityMetadata)
- Stores all entity data in flat JSONB structures
- Does NOT support relationships between different entity records (e.g., Customer → Organization)
- Cannot express "has many" or "belongs to one" relationships between entities

### Proposed Implementation (v2)

To implement relations similar to Twenty CRM:

1. **Add relation field types**:
```python
class FieldType(StrEnum):
    # ... existing types ...
    RELATION_HAS_MANY = "RELATION_HAS_MANY"
    RELATION_BELONGS_TO = "RELATION_BELONGS_TO"
```

2. **Extend FieldMetadata** with relation settings:
```python
# In field_settings JSONB:
{
    "relation_type": "has_many" | "belongs_to_one",
    "target_entity_id": UUID,  # References EntityMetadata
    "foreign_key_field": str,  # Field name in related entity
    "cascade_delete": bool,
    "backref_field": str  # Reverse relation field name
}
```

3. **Storage options**:
   - **Option A**: Store foreign keys in JSONB (current approach extension)
     - Pro: Maintains flat structure
     - Con: No referential integrity

   - **Option B**: Create junction tables for relations
     - Pro: True referential integrity
     - Con: More complex queries

   - **Option C**: Hybrid approach
     - Store IDs in JSONB for flexibility
     - Add PostgreSQL CHECK constraints for validation
     - Use triggers for cascade operations

4. **Query builder extensions**:
```python
# New operators for relations
await builder.has_related(entity_id, "customers", filters=[...])
await builder.belongs_to(entity_id, "organization", org_id)
```

### Why Relations Are Not in v1

1. **Complexity**: Relations require careful design for referential integrity
2. **Performance**: Need proper indexing strategy for JOIN operations
3. **Migration Path**: Easier to add relations later than change them
4. **JSONB Limitations**: PostgreSQL JSONB doesn't enforce foreign keys

## Future Enhancements (v2)

- **Nested Objects**: Support for complex nested structures
- **Required Fields**: Allow fields to be marked as required
- **Field Migrations**: Transform data when field types change
- **Computed Fields**: Derive values from other fields
- **Field Relationships**: Link between entity types (as described above)
- **Full-text Search**: PostgreSQL FTS integration

## References

- [SQLAlchemy 2.0 PostgreSQL JSONB Documentation](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#sqlalchemy.dialects.postgresql.JSONB)
- [PostgreSQL JSONB Operators](https://www.postgresql.org/docs/current/functions-json.html)
- [GIN Index Optimization](https://www.postgresql.org/docs/current/gin-intro.html)
