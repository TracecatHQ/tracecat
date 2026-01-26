import os
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class DBConfig:
    test_db_name: str
    base_url: str

    @property
    def test_url(self) -> str:
        return f"{self.base_url}{self.test_db_name}"

    @property
    def test_url_sync(self) -> str:
        return self.test_url.replace("+asyncpg", "+psycopg")

    @property
    def sys_url(self) -> str:
        return f"{self.base_url}postgres"

    @property
    def sys_url_sync(self) -> str:
        return self.sys_url.replace("+asyncpg", "+psycopg")


TEST_DB_NAME = f"test_db_{uuid.uuid4()}"
# Read port from environment for worktree cluster support (default: 5432)
_PG_PORT = os.environ.get("PG_PORT", "5432")
_IN_DOCKER = os.path.exists("/.dockerenv")
_DB_HOST = "postgres_db" if _IN_DOCKER else "localhost"
TEST_DB_URL_BASE = f"postgresql+asyncpg://postgres:postgres@{_DB_HOST}:{_PG_PORT}/"
TEST_DB_CONFIG = DBConfig(TEST_DB_NAME, TEST_DB_URL_BASE)
