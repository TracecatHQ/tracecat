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

type Cursor = tuple[float | str | None, int]
type Parameter = str | int | float | None


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
    cursor: Cursor | None = None,
) -> tuple[str, tuple[Parameter, ...]]:
    """Build matching candidates, then order/page them and fetch full source rows.

    Result columns start with score, cursor value and source row ID.
    """
    resolved = resolve_search_columns(
        search.selected or "all_columns", tuple(c.name for c in cols)
    )
    selected = [(i, c) for i, c in enumerate(cols) if c.name in resolved]
    if isinstance(search.order, ColumnSearchOrder):
        orderable = {"id", *(c.name for c in cols if c.kind == "text")}
        if search.order.column not in orderable:
            raise ValueError(
                "Ordering column is missing or unsupported; benchmark orders TEXT or row ID"
            )
    if search.mode == SearchMode.LITERAL:
        matches, params = _literal_matches(selected, search.text, projection)
    else:
        matches, params = _full_text_matches(selected, search.text, projection)
    statement, cursor_params = _page_query(matches, search.order, cursor)
    return statement, (*params, *cursor_params)


def _literal_matches(
    selected: list[tuple[int, Column]], text: str, projection: bool
) -> tuple[str, tuple[str, ...]]:
    pattern = literal_search_pattern(text) or ""
    if projection:
        column_ids = ",".join(str(i) for i, _ in selected)
        predicate = (
            "s.id IN (SELECT row_id FROM literal_projection "
            f"WHERE workspace = 1 AND column_id IN ({column_ids}) "
            "AND body ILIKE %s ESCAPE E'\\\\')"
        )
        params = (pattern,)
    else:
        predicate = (
            "("
            + " OR ".join(
                f"{column.literal} ILIKE %s ESCAPE E'\\\\'" for _, column in selected
            )
            + ")"
        )
        params = (pattern,) * len(selected)
    return (
        f"SELECT s.id, 0::real AS score FROM source s WHERE workspace = 1 AND {predicate}",
        params,
    )


def _full_text_matches(
    selected: list[tuple[int, Column]], text: str, projection: bool
) -> tuple[str, tuple[str, ...]]:
    units = parse_full_text_query(text)
    query_ctes = []
    for i, unit in enumerate(units):
        parser = "phraseto_tsquery" if unit.is_phrase else "plainto_tsquery"
        query_ctes.append(f"q{i} AS (SELECT {parser}('{CONFIG}', %s) AS q)")
    params = tuple(unit.text for unit in units)
    if projection:
        column_ids = ",".join(str(i) for i, _ in selected)
        hits = [
            f"h{i} AS (SELECT row_id, max(ts_rank_cd(vector, q{i}.q)) AS score "
            f"FROM projection CROSS JOIN q{i} WHERE workspace = 1 "
            f"AND column_id IN ({column_ids}) AND vector @@ q{i}.q GROUP BY row_id)"
            for i in range(len(units))
        ]
        joins = " ".join(f"JOIN h{i} USING(row_id)" for i in range(1, len(units)))
        score = " + ".join(f"h{i}.score" for i in range(len(units)))
        return (
            f"WITH {', '.join(query_ctes + hits)} SELECT row_id AS id, {score} AS score FROM h0 {joins}",
            params,
        )

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
            + ",".join(f"ts_rank_cd({column.vector}, q{i}.q)" for _, column in selected)
            + ")"
        )
    cross = ", ".join(f"q{i}" for i in range(len(units)))
    return (
        f"WITH {', '.join(query_ctes)} SELECT s.id, {' + '.join(scores)} AS score "
        f"FROM source s CROSS JOIN {cross} WHERE workspace = 1 AND "
        + " AND ".join(predicates),
        params,
    )


def _page_query(
    matches: str, order: SearchOrder, cursor: Cursor | None
) -> tuple[str, tuple[Parameter, ...]]:
    paging = ""
    params: tuple[Parameter, ...] = ()
    if isinstance(order, ColumnSearchOrder):
        matches = f"SELECT candidates.*, ordered.{order.column} AS order_value FROM ({matches}) candidates JOIN source ordered USING(id)"
        direction = order.direction.upper()
        ordering = f"order_value {direction} NULLS LAST, id {direction}"
        page_ordering = f"page.order_value {direction} NULLS LAST, page.id {direction}"
        cursor_column = "order_value"
        if cursor is not None:
            value, row_id = cursor
            operator = ">" if order.direction == "asc" else "<"
            if value is None:
                paging = f"WHERE order_value IS NULL AND id {operator} %s"
                params = (row_id,)
            else:
                paging = f"WHERE (order_value, id) {operator} (%s, %s) OR order_value IS NULL"
                params = cursor
    else:
        ordering = "score DESC, id DESC"
        page_ordering = "page.score DESC, page.id DESC"
        cursor_column = "score"
        if cursor is not None:
            paging = "WHERE (score, id) < (%s::real, %s::bigint)"
            params = cursor
    statement = (
        f"WITH matched AS ({matches}), page AS (SELECT *, {cursor_column} AS cursor_value FROM matched {paging} "
        f"ORDER BY {ordering} LIMIT 50) "
        "SELECT page.score, page.cursor_value, s.* FROM page JOIN source s USING(id) "
        f"ORDER BY {page_ordering}"
    )
    return statement, params
