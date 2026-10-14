"""Contract-consuming query shapes shared by all measured prototypes."""

from dataclasses import dataclass

from tracecat.query.lexical import (
    literal_search_pattern,
    parse_full_text_query,
    resolve_search_columns,
)

from .schema import CONFIG, Column


@dataclass(frozen=True, slots=True)
class Search:
    name: str
    text: str
    mode: str = "fts"
    selected: tuple[str, ...] = ()


SEARCHES = (
    Search("selective", "needle"),
    Search("cross_column", "needle jsonbeacon selectneedle multineedle"),
    Search("phrase", '"red fox"'),
    Search("json_key", "keyneedle", selected=("payload",)),
    Search("json_scalar", "jsonbeacon", selected=("payload",)),
    Search("select", "selectneedle", selected=("choice",)),
    Search("multi_select", "multineedle", selected=("choices",)),
    Search("broad", "common"),
    Search("literal_selective", "beacon", "trgm"),
    Search("literal_json_serialized", '"nested": {', "trgm", ("payload",)),
    Search("literal_broad", "com", "trgm"),
    Search("literal_no_trigrams", "!!!", "trgm"),
)


def query(
    cols: tuple[Column, ...],
    search: Search,
    *,
    projection: bool = False,
    cursor: tuple[float, int] | None = None,
) -> tuple[str, tuple[str | int | float, ...]]:
    """Return parameterized AND-of-OR search, rank ordering and keyset paging."""
    resolved = resolve_search_columns(
        search.selected or "all_columns", tuple(c.name for c in cols)
    )
    selected = [(i, c) for i, c in enumerate(cols) if c.name in resolved]
    if not selected:
        raise ValueError("Search requires columns")
    params: list[str | int | float] = []
    if search.mode == "trgm":
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
    paging = ""
    if cursor:
        paging = "WHERE (score, id) < (%s::real, %s::bigint)"
        params.extend(cursor)
    # Fetch the actual wide source row, rather than timing an ID-only response.
    statement = (
        f"WITH matched AS ({cte}), page AS (SELECT * FROM matched {paging} "
        "ORDER BY score DESC, id DESC LIMIT 50) "
        "SELECT page.score, s.* FROM page JOIN source s USING(id) "
        "ORDER BY page.score DESC, page.id DESC"
    )
    return statement, tuple(params)
