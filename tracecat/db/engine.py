import json
import os

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
    Action,
    CaseAction,
    UDFSpec,
    User,
    Webhook,
    Workflow,
)
from tracecat.registry import registry

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


def initialize_db() -> Engine:
    # Relational table
    engine = create_db_engine()
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
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


def clone_workflow(
    workflow: Workflow,
    session: Session,
    new_owner_id: str,
) -> Workflow:
    """
    Clones a Resource, including its relationships (up to a certain depth).

    :param model_instance: The SQLModel instance to clone.
    :param session: The SQLModel session.
    :param _depth: Current depth level, used to limit recursive depth.
    :return: The cloned instance.
    """

    # Create a new instance of the same model without the primary key
    cloned_workflow = Workflow(
        **workflow.model_dump(
            exclude={"id", "created_at", "updated_at", "owner_id", "object", "status"}
        ),
        owner_id=new_owner_id,
        status="offline",
    )

    # Iterate over relationships and clone them
    action_replacements = {}
    for action in workflow.actions:
        # Special treatment for webhook actions:
        # Need to update the action.path

        cloned_action = Action(
            owner_id=new_owner_id,
            workflow_id=cloned_workflow.id,
            **action.model_dump(
                exclude={"id", "created_at", "updated_at", "owner_id", "workflow_id"}
            ),
        )

        action_inputs: dict[str, str] = json.loads(cloned_action.inputs)
        if action.type == "webhook":
            cloned_webhook = Webhook(
                owner_id=new_owner_id,
                action_id=cloned_action.id,
                workflow_id=cloned_workflow.id,
            )
            # Update the action inputs to point to the new webhook path
            action_inputs.update(path=cloned_webhook.id, secret=cloned_webhook.secret)

            # Assert that there's a new computed secret
            session.add(cloned_webhook)
        cloned_action.inputs = json.dumps(action_inputs)
        action_replacements[action.id] = (cloned_action.id, action_inputs)
        session.add(cloned_action)

    # For each action in the workflow, update the workflow object
    graph = json.loads(workflow.object)
    for edge in graph["edges"]:
        edge["source"] = action_replacements[edge["source"]][0]
        edge["target"] = action_replacements[edge["target"]][0]
        edge["id"] = f"{edge['source']}-{edge['target']}"
    for node in graph["nodes"]:
        new_id, new_inputs = action_replacements[node["id"]]
        node["id"] = new_id
        node["data"].update(id=new_id, inputs=new_inputs, selected=False)
    cloned_workflow.object = json.dumps(graph)

    session.add(cloned_workflow)
    return cloned_workflow
