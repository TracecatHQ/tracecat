"""Live adversarial probes: write failures, dropped tails and value boundaries."""

from psycopg import Connection, Error
from pydantic import JsonValue

from tracecat.query.lexical_types import ColumnSearchOrder, SearchMode

from .queries import Search, query
from .schema import CONFIG, columns, populate_projection


def safety_probes(conn: Connection, max_bytes: int) -> list[dict[str, JsonValue]]:
    """Record PostgreSQL limits without letting failed indexes abort source writes."""
    results: list[dict[str, JsonValue]] = []
    for count in (255, 256, 257):
        value = "echo " * count + "beacon"
        matches = conn.execute(
            f"SELECT to_tsvector('{CONFIG}', %s) @@ phraseto_tsquery('{CONFIG}', 'echo beacon')",
            (value,),
        ).fetchone()[0]
        results.append(
            {
                "name": "repeated_position_boundary",
                "repetitions": count,
                "direct_matches": matches,
            }
        )
    for count in (16381, 16382, 16383):
        matches = conn.execute(
            f"SELECT to_tsvector('{CONFIG}', %s) @@ phraseto_tsquery('{CONFIG}', 'red fox')",
            ("pad " * count + "red fox",),
        ).fetchone()[0]
        results.append(
            {
                "name": "absolute_position_boundary",
                "prefix_words": count,
                "direct_matches": matches,
            }
        )
    for value in ("a" * 2047, "a" * 2048, "é" * 1023, "é" * 1024):
        count = conn.execute(
            f"SELECT length(to_tsvector('{CONFIG}', %s))", (value,)
        ).fetchone()[0]
        results.append(
            {
                "name": "lexeme_byte_boundary",
                "encoding_case": "ascii" if value.isascii() else "utf8_multibyte",
                "bytes": len(value.encode()),
                "lexemes": count,
            }
        )
    conn.execute(
        "CREATE TABLE safety_source(id int PRIMARY KEY, value text, doc jsonb)"
    )
    conn.execute(
        "CREATE TABLE safety_projection(source_id int PRIMARY KEY, vector tsvector NOT NULL)"
    )
    conn.execute(
        "CREATE INDEX safety_projection_fts ON safety_projection USING gin(vector)"
    )
    fixtures = [
        ("position_tail", "pad " * 20_000 + "red fox", "red fox", True),
        ("repeated_word_tail", "echo " * 300 + "beacon", "echo beacon", True),
        ("large_nested_tail", "pad " * (max_bytes // 4) + "red fox", "red fox", True),
        (
            "oversized_vector",
            " ".join(f"word{i:07}" for i in range(max_bytes // 12)),
            "word0000001",
            False,
        ),
        ("long_lexeme", "a" * 2048 + " tailbeacon", "tailbeacon", False),
    ]
    for identifier, (name, value, phrase, chunkable) in enumerate(fixtures, 1):
        conn.execute(
            "INSERT INTO safety_source VALUES (%s, %s, jsonb_build_object('nested', "
            "jsonb_build_object('value', %s::text)))",
            (identifier, value, value),
        )
        source_valid = conn.execute(
            "SELECT value = %s AND doc #>> '{nested,value}' = %s FROM safety_source WHERE id=%s",
            (value, value, identifier),
        ).fetchone()[0]
        probe: dict[str, JsonValue] = {
            "name": name,
            "source_bytes": len(value.encode()),
            "source_write_valid": source_valid,
        }
        try:
            with conn.transaction():
                direct = conn.execute(
                    f"SELECT to_tsvector('{CONFIG}', value) @@ phraseto_tsquery('{CONFIG}', %s), "
                    f"pg_column_size(to_tsvector('{CONFIG}', value)) FROM safety_source WHERE id=%s",
                    (phrase, identifier),
                ).fetchone()
                probe["direct_matches"] = direct[0]
                probe["vector_bytes"] = direct[1]
        except Error as error:
            probe["direct_sqlstate"] = error.sqlstate
        for kind, expression in (
            ("text", f"to_tsvector('{CONFIG}', value)"),
            ("jsonb", f"jsonb_to_tsvector('{CONFIG}', doc, '[\"all\"]'::jsonb)"),
        ):
            try:
                with conn.transaction():
                    conn.execute(
                        f"CREATE INDEX safety_{kind} ON safety_source USING gin (({expression}))"
                    )
                    conn.execute(f"DROP INDEX safety_{kind}")
                probe[f"{kind}_index_build"] = "ok"
            except Error as error:
                probe[f"{kind}_index_sqlstate"] = error.sqlstate
        if chunkable:
            chunk_result = conn.execute(
                f"SELECT bool_or(to_tsvector('{CONFIG}', body) @@ phraseto_tsquery('{CONFIG}', %s)), "
                f"count(*), max(pg_column_size(to_tsvector('{CONFIG}', body))) "
                "FROM value_chunks(%s)",
                (phrase, value),
            ).fetchone()
            probe["chunk_matches"] = chunk_result[0]
            probe["chunks"] = chunk_result[1]
            probe["max_chunk_vector_bytes"] = chunk_result[2]
            if not chunk_result[0]:
                raise AssertionError(f"Chunked tail omitted: {name}")
        # Fail an actual independent projection write in its own transaction.
        # This intentionally tests a worker's representation failure, not a
        # claim that the experimental chunker handles every possible value.
        try:
            with conn.transaction():
                conn.execute(
                    f"INSERT INTO safety_projection SELECT id, to_tsvector('{CONFIG}', value) FROM safety_source WHERE id=%s",
                    (identifier,),
                )
        except Error as error:
            probe["projection_write_sqlstate"] = error.sqlstate
            survived = conn.execute(
                "SELECT value = %s FROM safety_source WHERE id=%s", (value, identifier)
            ).fetchone()[0]
            published = conn.execute(
                "SELECT EXISTS(SELECT 1 FROM safety_projection WHERE source_id=%s)",
                (identifier,),
            ).fetchone()[0]
            probe["source_survives_projection_failure"] = survived
            probe["failed_projection_published"] = published
            if not survived or published:
                raise AssertionError(
                    "Projection failure changed source or published partial work"
                ) from error
        else:
            probe["projection_write_succeeded"] = True
        # Trigram indexing does not run to_tsvector. Check both existing-value
        # builds and a subsequent write, rather than assuming its safety from
        # the full-text results.
        conn.execute(
            "CREATE INDEX safety_trgm ON safety_source USING gin(value public.gin_trgm_ops)"
        )
        conn.execute(
            "UPDATE safety_source SET value=value || ' literalbeacon' WHERE id=%s",
            (identifier,),
        )
        # A tiny fixture normally favors a scan. Force GIN only in this safety
        # probe and record its plan; performance measurements never force it.
        with conn.transaction():
            conn.execute("SET LOCAL enable_seqscan = off")
            plan = conn.execute(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT id FROM safety_source WHERE value ILIKE %s",
                ("%literalbeacon%",),
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT id FROM safety_source WHERE value ILIKE %s",
                ("%literalbeacon%",),
            ).fetchall()
        if not plan_uses_index(plan, "safety_trgm") or rows != [(identifier,)]:
            raise AssertionError("GIN-backed tail retrieval failed")
        probe["trigram_write_and_tail_match"] = True
        probe["trigram_tail_plan"] = plan
        conn.execute("DROP INDEX safety_trgm")
        results.append(probe)
        conn.execute("TRUNCATE safety_source, safety_projection")
    # Demonstrate an already-built expression index rejecting a valid INSERT.
    conn.execute(
        f"CREATE INDEX unsafe_write ON safety_source USING gin(to_tsvector('{CONFIG}', value))"
    )
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO safety_source VALUES(1, %s, NULL)", (fixtures[3][1],)
            )
    except Error as error:
        results.append({"name": "indexed_insert_rejected", "sqlstate": error.sqlstate})
    else:
        raise AssertionError("Expected oversized tsvector rejection was not reproduced")
    conn.execute("DROP TABLE safety_source, safety_projection")
    return results


def plan_uses_index(plan: JsonValue, name: str) -> bool:
    """Inspect PostgreSQL's variable nested EXPLAIN JSON without string matching."""
    match plan:
        case {"Index Name": index} if index == name:
            return True
        case dict():
            return any(plan_uses_index(value, name) for value in plan.values())
        case list():
            return any(plan_uses_index(value, name) for value in plan)
        case _:
            return False


def check_contract(conn: Connection) -> list[dict[str, JsonValue]]:
    """Compare prototypes on small exact fixtures before timing any queries."""
    cols = columns(4)
    conn.execute("""
        INSERT INTO source(id,workspace,text_0,payload,choice,choices) VALUES
          (-1,1,'alpha red fox','{"nested":{"keyneedle":"beta"}}','gamma','["delta"]'),
          (-2,1,'alpha red','{"one":"fox","two":"red"}','normal','["red","fox"]'),
          (-3,2,'alpha red fox','{"nested":"beta"}','gamma','["delta"]'),
          (-4,1,'café HOST_name example.test 192.0.2.1 abc123','{"scalar":42,"flag":true}',NULL,'[]')
    """)
    # A phrase straddles a chunk boundary within ONE value. A second value must
    # never supply the other half of a phrase.
    conn.execute(
        "INSERT INTO source(id,workspace,text_0,payload,choice,choices) "
        "VALUES(-5,1,%s,'{}','normal','[]')",
        ("pad " * 127 + "red fox",),
    )
    conn.execute(
        "INSERT INTO source(id,workspace,text_0,payload,choice,choices) VALUES(-6,1,%s,'{}','normal','[]')",
        ("a" * 8190 + " boundary%_\nbeacon",),
    )
    conn.execute("""
        INSERT INTO source(id,workspace,text_0,payload,choice,choices)
        SELECT -g,1,'orderingprobe','{}',
               CASE WHEN g % 3=0 THEN NULL WHEN g % 3=1 THEN 'alpha' ELSE 'zeta' END,'[]'
        FROM generate_series(100,229) g
    """)
    conn.execute(
        "INSERT INTO source(id,workspace,text_0,payload,choice,choices) VALUES(-230,2,'orderingprobe','{}','zeta','[]')"
    )
    populate_projection(conn, cols, after=-231)
    cases = (
        (Search("cross_columns", "alpha beta gamma delta"), {-1}),
        (Search("phrase_boundaries", '"red fox"'), {-1, -5}),
        (Search("json_key", "keyneedle", selected=("payload",)), {-1}),
        (Search("json_scalars", "42 true", selected=("payload",)), {-4}),
        (Search("unicode", "café"), {-4}),
        (Search("identifier", "HOST_name example.test 192.0.2.1 abc123"), {-4}),
        (
            Search(
                "serialized",
                '"nested": {',
                SearchMode.LITERAL,
                order=ColumnSearchOrder("choice"),
            ),
            {-1},
        ),
        (
            Search(
                "escaped_wildcard",
                "abc%",
                SearchMode.LITERAL,
                order=ColumnSearchOrder("choice"),
            ),
            set(),
        ),
        (
            Search(
                "literal_chunk_boundary",
                "boundary%_\nbeacon",
                SearchMode.LITERAL,
                order=ColumnSearchOrder("choice"),
            ),
            {-6},
        ),
    )
    results: list[dict[str, JsonValue]] = []
    for search, expected in cases:
        for projection in (False, True):
            statement, params = query(cols, search, projection=projection)
            actual = {row[2] for row in conn.execute(statement, params)}
            if actual != expected:
                raise AssertionError(
                    f"{search.name}: projection={projection}, {actual} != {expected}"
                )
        results.append({"name": search.name, "passed": True})
    for direction in ("asc", "desc"):
        search = Search(
            "column_order",
            "orderingprobe",
            SearchMode.LITERAL,
            order=ColumnSearchOrder("choice", direction),
        )
        expected = [
            r[0]
            for r in conn.execute(
                f"SELECT id FROM source WHERE workspace=1 AND text_0='orderingprobe' ORDER BY choice {direction} NULLS LAST,id {direction}"
            )
        ]
        for projection in (False, True):
            actual: list[int] = []
            cursor = None
            while True:
                statement, params = query(
                    cols, search, projection=projection, cursor=cursor
                )
                page = conn.execute(statement, params).fetchall()
                if not page:
                    break
                actual.extend(r[2] for r in page)
                cursor = (page[-1][1], page[-1][2])
                if len(actual) > len(expected):
                    raise AssertionError("Column-order pagination duplicated rows")
            if actual != expected:
                raise AssertionError(
                    "Column-order pagination omitted, reordered or leaked rows"
                )
        results.append({"name": f"column_order_{direction}_nulls_last", "passed": True})
    conn.execute("TRUNCATE source, projection, literal_projection")
    return results
