#!/usr/bin/env python3
import os

from psycopg import connect

u = os.environ["TRACECAT__DB_USER"]
p = os.environ["TRACECAT__DB_PASS"]
h = os.environ["TRACECAT__DB_ENDPOINT"]
pt = int(os.getenv("TRACECAT__DB_PORT", "5432"))

with connect(
    host=h,
    port=pt,
    dbname=os.getenv("TRACECAT__DB_NAME", "postgres"),
    user=u,
    password=p,
) as c:
    cur = c.cursor()
    cur.execute(
        "SELECT current_user,rolcreatedb FROM pg_roles WHERE rolname=current_user"
    )
    print("INFO", cur.fetchone())

for db in ("temporal", "temporal_visibility"):
    try:
        connect(host=h, port=pt, dbname=db, user=u, password=p).close()
        print("OK", db)
    except Exception as e:
        print("FAIL", db, e)
