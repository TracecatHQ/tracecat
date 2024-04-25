"""Query (Postgres SQL syntax) to Polars builder and execution.

Supported SQL keywords: https://docs.rs/polars-sql/latest/src/polars_sql/keywords.rs.html
"""

import polars as pl


def pl_sql_query(
    lf: pl.LazyFrame, query: str, eager: bool = False
) -> pl.DataFrame | pl.LazyFrame:
    with pl.SQLContext(table=lf) as ctx:
        lf = ctx.execute(query)
    if eager:
        return lf.collect()
    return lf
