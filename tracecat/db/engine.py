import os

import lancedb
from loguru import logger
from sqlalchemy import Engine
from sqlmodel import (
    Session,
    SQLModel,
    create_engine,
    delete,
    select,
)

from tracecat import config
from tracecat.db.schemas import (
    DEFAULT_CASE_ACTIONS,
    CaseAction,
    CaseContext,
    CaseSchema,
    UDFSpec,
    User,
)
from tracecat.labels.mitre import get_mitre_tactics_techniques
from tracecat.registry import registry

STORAGE_PATH = config.TRACECAT_DIR / "storage"
STORAGE_PATH.mkdir(parents=True, exist_ok=True)

_engine: Engine = None


def create_db_engine() -> Engine:
    if config.TRACECAT__APP_ENV == "production":
        # Postgres
        sslmode = os.getenv("TRACECAT__DB_SSLMODE", "require")
        engine_kwargs = {
            "pool_timeout": 30,
            "pool_recycle": 3600,
            "connect_args": {"sslmode": sslmode},
        }
    elif config.TRACECAT__APP_ENV == "local":
        # SQLite disk-based database
        engine_kwargs = {"connect_args": {"check_same_thread": False}}
    else:
        # Postgres as default
        engine_kwargs = {
            "pool_timeout": 30,
            "pool_recycle": 3600,
            "connect_args": {"sslmode": "disable"},
        }
    if config.TRACECAT__DB_USER and config.TRACECAT__DB_PASS:
        db_name = config.TRACECAT__DB_NAME
        username = config.TRACECAT__DB_USER
        password = config.TRACECAT__DB_PASS
        address = f"{config.TRACECAT__DB_ENDPOINT}:{config.TRACECAT__DB_PORT}"
        uri = f"postgresql+psycopg://{username}:{password}@{address}/{db_name}"
    else:
        uri = config.TRACECAT__DB_URI

    engine = create_engine(uri, **engine_kwargs)
    return engine


def create_vdb_conn() -> lancedb.DBConnection:
    if os.environ.get("LANCEDB__S3_STORAGE_PATH") is None:
        db = lancedb.connect(STORAGE_PATH / "vector.db")
    else:
        db = lancedb.connect(os.environ["LANCEDB__S3_STORAGE_PATH"])
    return db


def initialize_db() -> Engine:
    # Relational table
    engine = create_db_engine()
    SQLModel.metadata.create_all(engine)

    # VectorDB
    db = create_vdb_conn()
    db.create_table("cases", schema=CaseSchema, exist_ok=True)

    with Session(engine) as session:
        # Add TTPs to context table only if context table is empty
        case_contexts_count = session.exec(select(CaseContext)).all()
        if len(case_contexts_count) == 0:
            mitre_labels = get_mitre_tactics_techniques()
            mitre_contexts = [
                CaseContext(owner_id="tracecat", tag="mitre", value=label)
                for label in mitre_labels
            ]
            session.add_all(mitre_contexts)
            session.commit()
            logger.info("Added default MITRE labels to case context table.")

        case_actions_count = session.exec(select(CaseAction)).all()
        if len(case_actions_count) == 0:
            default_actions = [
                CaseAction(owner_id="tracecat", tag="case_action", value=case_action)
                for case_action in DEFAULT_CASE_ACTIONS
            ]
            session.add_all(default_actions)
            session.commit()
            logger.info("Added default case actions to case action table.")
        # We might be ok just overwriting the integrations table?
        # Add integrations to integrations table regardless of whether it's empty
        session.exec(delete(UDFSpec))
        registry.init()
        udfs = [udf.to_udf_spec() for _, udf in registry]
        logger.info("Initializing UDF registry with default UDFs.", n=len(udfs))
        session.add_all(udfs)
        session.commit()

        user_id = "default-tracecat-user"
        result = session.exec(select(User).where(User.id == user_id).limit(1))
        if not result.one_or_none():
            # Create a default user if it doesn't exist
            user = User(owner_id="tracecat", id=user_id)
            session.add(user)
            session.commit()
    return engine


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = initialize_db()
    return _engine
