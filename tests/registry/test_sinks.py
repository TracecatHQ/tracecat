import sqlite3

import pytest
from tracecat_registry.base.etl.sinks import write_to_database


@pytest.fixture
def setup_database(tmp_path):
    file_path = tmp_path / "test_db.sqlite"
    uri = f"sqlite:///{file_path}"

    with sqlite3.connect(file_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE test_table (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                age INTEGER NOT NULL
            )
        """)
        conn.commit()
        yield uri, file_path


def test_write_to_database(setup_database):
    uri, file_path = setup_database
    data = [{"id": 1, "name": "Alice", "age": 30}, {"id": 2, "name": "Bob", "age": 25}]
    table_name = "test_table"
    if_table_exists = "append"

    n_rows = write_to_database(
        data=data,
        table_name=table_name,
        uri=uri,
        if_table_exists=if_table_exists,
    )

    with sqlite3.connect(file_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM test_table")
        rows = cursor.fetchall()

    assert n_rows == len(rows)
    assert rows[0] == (1, "Alice", 30)
    assert rows[1] == (2, "Bob", 25)
