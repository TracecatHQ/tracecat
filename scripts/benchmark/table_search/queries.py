"""Contract-consuming query shapes shared by all measured prototypes."""

from dataclasses import dataclass
from typing import Literal

from tracecat.query.lexical import (
    literal_search_pattern,
    parse_full_text_query,
    resolve_search_columns,
)
from tracecat.query.lexical_types import (
    ColumnSearchOrder,
    LexicalSearchOptions,
    SearchMode,
    SearchOrder,
)

from .schema import CONFIG, Column


@dataclass(frozen=True, slots=True)
class Search:
    name: str
    text: str
    mode: SearchMode = SearchMode.FULL_TEXT
    selected: tuple[str, ...] = ()
    order: SearchOrder = "relevance"
    sampling: Literal["selective", "broad"] = "selective"
    concurrent_for: tuple[Literal["fts", "trgm", "chunked"], ...] = ()

    def __post_init__(self) -> None:
        LexicalSearchOptions(self.mode, self.selected or "all_columns", self.order)


SEARCHES = (
    Search("selective", "needle", concurrent_for=("fts", "chunked")),
    Search("cross_column", "needle jsonbeacon selectneedle multineedle"),
    Search("phrase", '"red fox"'),
    Search("json_key", "keyneedle", selected=("payload",), sampling="broad"),
    Search("json_scalar", "jsonbeacon", selected=("payload",)),
    Search("select", "selectneedle", selected=("choice",)),
    Search("multi_select", "multineedle", selected=("choices",)),
    Search("broad", "common", sampling="broad"),
    Search(
        "literal_selective",
        "beacon",
        SearchMode.LITERAL,
        order=ColumnSearchOrder("choice"),
        concurrent_for=("trgm",),
    ),
    Search(
        "literal_json_serialized",
        '"nested": {',
        SearchMode.LITERAL,
        ("payload",),
        order=ColumnSearchOrder("choice"),
        sampling="broad",
    ),
    Search(
        "literal_broad",
        "com",
        SearchMode.LITERAL,
        order=ColumnSearchOrder("choice"),
        sampling="broad",
    ),
    Search(
        "literal_no_trigrams",
        "!!!",
        SearchMode.LITERAL,
        order=ColumnSearchOrder("choice"),
        sampling="broad",
    ),
)


def query(
    cols: tuple[Column, ...],
    search: Search,
    *,
    projection: bool = False,
    cursor: tuple[float | str | None, int] | None = None,
) -> tuple[str, tuple[str | int | float | None, ...]]:
    """Return parameterized AND-of-OR search, rank ordering and keyset paging."""
    resolved = resolve_search_columns(
        search.selected or "all_columns", tuple(c.name for c in cols)
    )
    selected = [(i, c) for i, c in enumerate(cols) if c.name in resolved]
    if not selected:
        raise ValueError("Search requires columns")
    params: list[str | int | float | None] = []
    ordering = "score DESC, id DESC"
    page_ordering = "page.score DESC, page.id DESC"
    cursor_value = "score"
    if isinstance(search.order, ColumnSearchOrder):
        if search.order.column not in {
            "id",
            *(c.name for c in cols if c.kind == "text"),
        }:
            raise ValueError(
                "Ordering column is missing or unsupported; benchmark orders TEXT or row ID"
            )
        direction = search.order.direction.upper()
        ordering = f"order_value {direction} NULLS LAST, id {direction}"
        page_ordering = f"page.order_value {direction} NULLS LAST, page.id {direction}"
        cursor_value = "order_value"
    if search.mode == SearchMode.LITERAL:
        pattern = literal_search_pattern(search.text)
        params.append(pattern or "")
        if projection:
            predicate = (
                "s.id IN (SELECT row_id FROM literal_projection "
                "WHERE workspace = 1 AND column_id IN ("
                + ",".join(str(i) for i, _ in selected)
                + ") AND body ILIKE %s ESCAPE E'\\\\')"
            )
        else:
            predicate = (
                "("
                + " OR ".join(
                    f"{c.literal} ILIKE %s ESCAPE E'\\\\'" for _, c in selected
                )
                + ")"
            )
            params *= len(selected)
        cte = f"SELECT s.id, 0::real AS score FROM source s WHERE workspace = 1 AND {predicate}"
    else:
        units = parse_full_text_query(search.text)
        query_ctes = []
        for i, unit in enumerate(units):
            parser = "phraseto_tsquery" if unit.is_phrase else "plainto_tsquery"
            query_ctes.append(f"q{i} AS (SELECT {parser}('{CONFIG}', %s) AS q)")
            params.append(unit.text)
        if projection:
            hits = []
            for i, _ in enumerate(units):
                hits.append(
                    f"h{i} AS (SELECT row_id, max(ts_rank_cd(vector, q{i}.q)) AS score "
                    f"FROM projection CROSS JOIN q{i} WHERE workspace = 1 "
                    "AND column_id IN ("
                    + ",".join(str(j) for j, _ in selected)
                    + f") AND vector @@ q{i}.q GROUP BY row_id)"
                )
            joins = " ".join(f"JOIN h{i} USING(row_id)" for i in range(1, len(units)))
            score = " + ".join(f"h{i}.score" for i in range(len(units)))
            cte = f"WITH {', '.join(query_ctes + hits)} SELECT row_id AS id, {score} AS score FROM h0 {joins}"
        else:
            predicates = []
            scores = []
            for i, unit in enumerate(units):
                matches = []
                for _, column in selected:
                    match = f"{column.vector} @@ q{i}.q"
                    if unit.is_phrase and column.kind == "jsonb":
                        match += (
                            f" AND EXISTS (SELECT 1 FROM {column.values} v "
                            f"WHERE to_tsvector('{CONFIG}', v.value) @@ q{i}.q)"
                        )
                    matches.append(f"({match})")
                predicates.append("(" + " OR ".join(matches) + ")")
                scores.append(
                    "greatest("
                    + ",".join(f"ts_rank_cd({c.vector}, q{i}.q)" for _, c in selected)
                    + ")"
                )
            cross = ", ".join(f"q{i}" for i in range(len(units)))
            cte = (
                f"WITH {', '.join(query_ctes)} SELECT s.id, {' + '.join(scores)} AS score "
                f"FROM source s CROSS JOIN {cross} WHERE workspace = 1 AND "
                + " AND ".join(predicates)
            )
    if isinstance(search.order, ColumnSearchOrder):
        cte = f"SELECT candidates.*, ordered.{search.order.column} AS order_value FROM ({cte}) candidates JOIN source ordered USING(id)"
    paging = ""
    if cursor:
        if isinstance(search.order, ColumnSearchOrder):
            operator = ">" if search.order.direction == "asc" else "<"
            if cursor[0] is None:
                paging = f"WHERE order_value IS NULL AND id {operator} %s"
                params.append(cursor[1])
            else:
                paging = f"WHERE (order_value, id) {operator} (%s, %s) OR order_value IS NULL"
                params.extend(cursor)
        else:
            paging = "WHERE (score, id) < (%s::real, %s::bigint)"
            params.extend(cursor)
    # Fetch the actual wide source row, rather than timing an ID-only response.
    statement = (
        f"WITH matched AS ({cte}), page AS (SELECT *, {cursor_value} AS cursor_value FROM matched {paging} "
        f"ORDER BY {ordering} LIMIT 50) "
        "SELECT page.score, page.cursor_value, s.* FROM page JOIN source s USING(id) "
        f"ORDER BY {page_ordering}"
    )
    return statement, tuple(params)
