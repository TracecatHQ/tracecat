"""Physical prototypes and deterministic, entirely synthetic datasets."""

from dataclasses import dataclass

from psycopg import Connection, sql

from tracecat.query.lexical_types import TEXT_SEARCH_CONFIG as CONFIG


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    kind: str

    @property
    def literal(self) -> str:
        # Names are generated here, never supplied by users.
        return f"coalesce({self.name}::text, '')"

    @property
    def vector(self) -> str:
        if self.kind == "jsonb":
            return f"jsonb_to_tsvector('{CONFIG}'::regconfig, {self.name}, '[\"all\"]'::jsonb)"
        return f"to_tsvector('{CONFIG}'::regconfig, {self.literal})"

    @property
    def values(self) -> str:
        if self.kind == "jsonb":
            return f"json_values({self.name})"
        return f"unnest(ARRAY[{self.literal}])"


def columns(width: int) -> tuple[Column, ...]:
    """Return width searchable columns spanning all four supported types."""
    if not 4 <= width <= 64:
        raise ValueError("Width must be between 4 and 64")
    return (
        *(Column(f"text_{i}", "text") for i in range(width - 3)),
        Column("payload", "jsonb"),
        Column("choice", "text"),
        Column("choices", "jsonb"),
    )


HELPERS = """
CREATE FUNCTION json_values(doc jsonb) RETURNS TABLE(value text)
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  WITH RECURSIVE nodes(doc) AS (
    SELECT doc
    UNION ALL
    SELECT child.doc FROM nodes n CROSS JOIN LATERAL (
      SELECT e.value AS doc FROM jsonb_each(
        CASE WHEN jsonb_typeof(n.doc) = 'object' THEN n.doc ELSE '{}' END) e
      UNION ALL
      SELECT a.value FROM jsonb_array_elements(
        CASE WHEN jsonb_typeof(n.doc) = 'array' THEN n.doc ELSE '[]' END) a
    ) child
  )
  SELECT e.key FROM nodes n CROSS JOIN LATERAL jsonb_each(
    CASE WHEN jsonb_typeof(n.doc) = 'object' THEN n.doc ELSE '{}' END) e
  UNION ALL
  SELECT doc #>> '{}' FROM nodes
  WHERE jsonb_typeof(doc) IN ('string', 'number', 'boolean')
$$;

-- Experimental whitespace chunks, NOT a production PostgreSQL tokenizer.
-- Only the bounded synthetic corpus is published by this prototype.
CREATE FUNCTION value_chunks(value text) RETURNS TABLE(ordinal int, body text)
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  WITH words AS (SELECT regexp_split_to_array(value, E'\\\\s+') AS tokens)
  SELECT start, array_to_string(tokens[start:start+127], ' ')
  FROM words, generate_series(1, array_length(tokens, 1), 97) start
$$;

CREATE TABLE projection (
  workspace int NOT NULL, row_id bigint NOT NULL, column_id int NOT NULL,
  value_id bigint NOT NULL, ordinal int NOT NULL, body text NOT NULL,
  vector tsvector NOT NULL,
  PRIMARY KEY (workspace, row_id, column_id, value_id, ordinal)
);
CREATE TABLE literal_projection (
  workspace int NOT NULL, row_id bigint NOT NULL, column_id int NOT NULL,
  ordinal int NOT NULL, body text NOT NULL,
  PRIMARY KEY (workspace, row_id, column_id, ordinal)
);
"""


def create_source(conn: Connection, cols: tuple[Column, ...]) -> None:
    """Create source rows; search experiments never touch application tables."""
    definitions = ", ".join(f"{c.name} {c.kind}" for c in cols)
    conn.execute(
        f"CREATE TABLE source (id bigint PRIMARY KEY, workspace int NOT NULL, "
        f"revision bigint NOT NULL DEFAULT 1, {definitions})"
    )
    conn.execute("CREATE INDEX source_workspace_id ON source(workspace, id)")
    conn.execute(HELPERS)


def insert_sql(cols: tuple[Column, ...]) -> str:
    """Server-side seeded generator, avoiding client serialization bottlenecks."""
    text_columns = []
    for i, _column in enumerate(cols[:-3]):
        text_columns.append(
            "'common event synthetic ' || "
            "CASE WHEN (g + %(seed)s) %% 997 = 0 THEN 'needle beacon ' ELSE '' END || "
            "CASE WHEN (g + %(seed)s) %% 991 = 0 THEN 'red fox ' ELSE '' END || "
            f"'field{i} café HOST_name example.test 192.0.2.1 abc123'"
        )
    return (
        f"INSERT INTO source (id, workspace, {', '.join(c.name for c in cols)}) "
        f"SELECT g, 1 + g %% %(workspaces)s, {', '.join(text_columns)}, "
        "jsonb_build_object('nested', jsonb_build_object('keyneedle', "
        "CASE WHEN (g + %(seed)s) %% 997 = 0 THEN 'jsonbeacon' ELSE 'normal' END), "
        "'array', jsonb_build_array('red', 'fox', 42, true)), "
        "CASE WHEN (g + %(seed)s) %% 997 = 0 THEN 'selectneedle' ELSE 'normal' END, "
        "jsonb_build_array('common', CASE WHEN (g + %(seed)s) %% 997 = 0 "
        "THEN 'multineedle' ELSE 'normal' END) "
        "FROM generate_series(%(start)s::bigint, %(end)s::bigint) g"
    )


def build_indexes(conn: Connection, cols: tuple[Column, ...], mode: str) -> None:
    """Use exactly the same expressions for the indexes and query predicates."""
    for column in cols:
        expression = column.vector if mode == "fts" else column.literal
        opclass = "" if mode == "fts" else " public.gin_trgm_ops"
        conn.execute(
            f"CREATE INDEX {column.name}_{mode} ON source USING gin "
            f"(({expression}){opclass})"
        )


def drop_indexes(conn: Connection, cols: tuple[Column, ...], mode: str) -> None:
    for column in cols:
        conn.execute(
            sql.SQL("DROP INDEX {} ").format(sql.Identifier(f"{column.name}_{mode}"))
        )


def populate_projection(
    conn: Connection, cols: tuple[Column, ...], *, after: int = 0
) -> None:
    """Preserve JSON key/scalar and MULTI_SELECT element identities."""
    for column_id, column in enumerate(cols):
        conn.execute(
            f"INSERT INTO projection SELECT workspace, id, {column_id}, "
            f"v.value_id, c.ordinal, c.body, to_tsvector('{CONFIG}', c.body) "
            f"FROM source CROSS JOIN LATERAL {column.values} "
            "WITH ORDINALITY v(value, value_id) "
            "CROSS JOIN LATERAL value_chunks(v.value) c WHERE id > %s",
            (after,),
        )
        # Serialized JSON stays intact for literal compatibility. Query input is
        # bounded to 1000 characters, so 999 characters of overlap cover every
        # possible boundary-spanning literal, including whitespace/punctuation.
        conn.execute(
            "INSERT INTO literal_projection SELECT workspace, id, "
            f"{column_id}, start, substring({column.literal} FROM start FOR 8192) "
            "FROM source CROSS JOIN LATERAL "
            f"generate_series(1, greatest(1, length({column.literal})), 7193) start "
            "WHERE id > %s",
            (after,),
        )
