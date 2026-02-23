from builtins import filter as filter_
from builtins import map as map_
from typing import Annotated, Any, Literal

from typing_extensions import Doc
import hashlib
import json
import os
import redis.asyncio as redis
import orjson
from tracecat_registry import ActionIsInterfaceError, registry
from tracecat_registry._internal.flatten import flatten_dict as _flatten_dict
from tracecat_registry._internal.jsonpath import eval_jsonpath
from tracecat_registry._internal.safe_lambda import build_safe_lambda
from tracecat_registry.context import get_context


def _resolve_dedup_scope() -> str:
    """Resolve the deduplication scope identifier.

    Prefer the registry execution context workspace ID. Fall back to the
    workspace ID environment variable when context isn't set (e.g. some tests).
    Raise explicitly when no workspace scope is available.
    """

    try:
        workspace_id = get_context().workspace_id
    except RuntimeError:
        workspace_id = os.environ.get("TRACECAT__WORKSPACE_ID")

    if not workspace_id:
        raise ValueError(
            "Deduplication could not determine this run's workspace scope. "
            "This indicates a platform execution-context issue. Retry the run; "
            "if it persists, contact your Tracecat administrator with the run ID."
        )
    return workspace_id


@registry.register(
    default_title="Reshape",
    description="Reshapes the input value to the output. You can use this to reshape a JSON-like structure into another easier to manipulate JSON object.",
    display_group="Data Transform",
    namespace="core.transform",
)
def reshape(
    value: Annotated[
        Any | list[Any] | dict[str, Any],
        Doc("The value to reshape"),
    ],
) -> Any:
    return value


@registry.register(
    default_title="Filter",
    description="Filter a collection using a Python lambda function.",
    display_group="Data Transform",
    namespace="core.transform",
)
def filter(
    items: Annotated[
        list[Any],
        Doc("Items to filter."),
    ],
    python_lambda: Annotated[
        str,
        Doc(
            'Filter condition as a Python lambda expression (e.g. `"lambda x: x > 2"`).'
        ),
    ],
) -> Any:
    fn = build_safe_lambda(python_lambda)
    return list(filter_(fn, items))


@registry.register(
    default_title="Is in",
    description="Filters items in a list based on whether they are in a collection.",
    display_group="Data Transform",
    namespace="core.transform",
)
def is_in(
    items: Annotated[
        list[Any],
        Doc("Items to filter."),
    ],
    collection: Annotated[
        list[Any],
        Doc("Collection of hashable items to check against."),
    ],
    python_lambda: Annotated[
        str | None,
        Doc(
            "Python lambda applied to each item before checking membership (e.g. `\"lambda x: x.get('name')\"`). Similar to `key` in the Python `sorted` function."
        ),
    ] = None,
) -> list[Any]:
    col_set = set(collection)
    if python_lambda:
        fn = build_safe_lambda(python_lambda)
        result = [item for item in items if fn(item) in col_set]
    else:
        result = [item for item in items if item in col_set]
    return result


@registry.register(
    default_title="Not in",
    description="Filters items in a list based on whether they are not in a collection.",
    display_group="Data Transform",
    namespace="core.transform",
)
def not_in(
    items: Annotated[
        list[Any],
        Doc("Items to filter."),
    ],
    collection: Annotated[
        list[Any],
        Doc("Collection of hashable items to check against."),
    ],
    python_lambda: Annotated[
        str | None,
        Doc(
            "Python lambda applied to each item before checking membership (e.g. `\"lambda x: x.get('name')\"`). Similar to `key` in the Python `sorted` function."
        ),
    ] = None,
) -> list[Any]:
    col_set = set(collection)
    if python_lambda:
        fn = build_safe_lambda(python_lambda)
        result = [item for item in items if fn(item) not in col_set]
    else:
        result = [item for item in items if item not in col_set]
    return result


async def _deduplicate_redis(
    seen: dict[tuple[Any, ...], dict[str, Any]], expire_seconds: int
) -> list[dict[str, Any]]:
    # Create Redis client directly to avoid event loop issues with Ray

    try:
        # Get Redis URL from environment
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        # Create a new Redis client in the current event loop
        redis_client = redis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=10,
        )
        redis_available = True
    except Exception as e:
        raise ConnectionError(
            f"Unable to connect to key-value store for deduplication: {e}"
        )

    result: list[dict[str, Any]] = []
    dedup_scope = _resolve_dedup_scope()

    try:
        # AWS ElastiCache usually adds ~0.3-1 ms RTT per command. Reduce round-trips
        # with a pipeline when we have more than a few items.
        if redis_available and len(seen) > 10:
            # Use async pipeline (transaction=False keeps commands independent)
            pipe = redis_client.pipeline(transaction=False)
            redis_keys: list[str] = []

            for key in seen.keys():
                key_str = json.dumps(key, sort_keys=True, default=str)
                redis_key = (
                    f"dedup:{dedup_scope}:"
                    f"{hashlib.sha256(key_str.encode()).hexdigest()}"
                )
                redis_keys.append(redis_key)
                pipe.set(redis_key, "1", ex=expire_seconds, nx=True)

            try:
                exec_results = await pipe.execute()
            except Exception as e:
                raise ConnectionError(f"Key-value store pipeline failed: {e}")

            # Determine which items are new globally based on pipeline results.
            for (key, item), was_set in zip(seen.items(), exec_results):
                if was_set:
                    result.append(item)
        else:
            # Sequential path (small batches pay negligible RTT penalty)
            for key, item in seen.items():
                is_new_globally = True

                if redis_available:
                    key_str = json.dumps(key, sort_keys=True, default=str)
                    redis_key = (
                        f"dedup:{dedup_scope}:"
                        f"{hashlib.sha256(key_str.encode()).hexdigest()}"
                    )

                    try:
                        was_set = await redis_client.set(
                            redis_key,
                            "1",
                            ex=expire_seconds,
                            nx=True,
                        )
                        is_new_globally = bool(was_set)
                    except Exception as e:
                        raise ConnectionError(
                            f"Unable to connect to key-value store for deduplication: {e}"
                        )

                if is_new_globally:
                    result.append(item)
    finally:
        # Clean up Redis connection
        if redis_available:
            await redis_client.aclose()

    return result


@registry.register(
    default_title="Deduplicate",
    description="Deduplicate a JSON object or a list of JSON objects given a list of keys. Returns a list of deduplicated JSON objects.",
    display_group="Data Transform",
    namespace="core.transform",
)
async def deduplicate(
    items: Annotated[
        dict[str, Any] | list[dict[str, Any]],
        Doc("JSON object or list of JSON objects to deduplicate."),
    ],
    keys: Annotated[
        list[str],
        Doc(
            "List of JSONPath keys to deduplicate by. Supports dot notation for nested keys (e.g. `['user.id']`)."
        ),
    ],
    expire_seconds: Annotated[
        int,
        Doc("Time to live for the deduplicated items in seconds. Defaults to 1 hour."),
    ] = 3600,
    persist: Annotated[
        bool,
        Doc(
            "Whether to persist deduplicated items across calls. If True, deduplicates across calls. If False, deduplicates within the current call only."
        ),
    ] = True,
) -> list[dict[str, Any]]:
    if not items:
        return []

    # Normalize input to list
    items_list = [items] if isinstance(items, dict) else items

    def get_nested_values(item: dict[str, Any], keys: list[str]) -> tuple[Any, ...]:
        values = []
        for key in keys:
            # Convert dot notation to jsonpath format
            jsonpath_expr = "$." + key
            value = eval_jsonpath(jsonpath_expr, item, strict=True)
            # Convert unhashable types to JSON strings for use as dict keys
            if isinstance(value, (list, dict)):
                value = json.dumps(value, sort_keys=True, default=str)
            values.append(value)
        return tuple(values)

    seen = {}
    results = []

    for item in items_list:
        key = get_nested_values(item, keys)

        # Always update or add to seen dict for within-call deduplication
        if key in seen:
            # Update existing item with same key
            seen[key].update(item)
        else:
            # First time seeing this key in this call
            seen[key] = item.copy()

    if persist:
        results = await _deduplicate_redis(seen, expire_seconds)
    else:
        results = list(seen.values())

    return results


@registry.register(
    default_title="Is duplicate",
    description="Check if a JSON object was recently seen.",
    display_group="Data Transform",
    namespace="core.transform",
)
async def is_duplicate(
    item: Annotated[
        dict[str, Any],
        Doc("JSON object to check."),
    ],
    keys: Annotated[
        list[str],
        Doc("List of JSONPath keys to check."),
    ],
    expire_seconds: Annotated[
        int,
        Doc("Time to live for the deduplicated items in seconds. Defaults to 1 hour."),
    ] = 3600,
) -> bool:
    result = await deduplicate(item, keys, expire_seconds=expire_seconds, persist=True)
    return len(result) == 0


@registry.register(
    default_title="Apply",
    description="Apply a Python lambda function to a value.",
    display_group="Data Transform",
    namespace="core.transform",
)
def apply(
    value: Annotated[
        Any,
        Doc("Value to apply the lambda function to."),
    ],
    python_lambda: Annotated[
        str,
        Doc("Python lambda function as a string (e.g. `\"lambda x: x.get('name')\"`)."),
    ],
) -> Any:
    fn = build_safe_lambda(python_lambda)
    return fn(value)


@registry.register(
    default_title="Map",
    description="Map a Python lambda function to each item in a list.",
    display_group="Data Transform",
    namespace="core.transform",
)
def map(
    items: Annotated[
        list[Any],
        Doc("Items to map the lambda function to."),
    ],
    python_lambda: Annotated[
        str,
        Doc("Python lambda function as a string (e.g. `\"lambda x: x.get('name')\"`)."),
    ],
) -> list[Any]:
    fn = build_safe_lambda(python_lambda)
    return list(map_(fn, items))


@registry.register(
    default_title="Drop nulls",
    description="Remove null values from a list.",
    display_group="Data Transform",
    namespace="core.transform",
)
def drop_nulls(
    items: Annotated[list[Any], Doc("List of items to filter.")],
) -> list[Any]:
    return [item for item in items if item is not None]


@registry.register(
    default_title="Scatter",
    description=(
        "Transform a collection of items into parallel execution streams, "
        "where each item is processed independently."
    ),
    display_group="Data Transform",
    namespace="core.transform",
)
def scatter(
    collection: Annotated[
        str | list[Any],
        Doc(
            "The collection to scatter. Each item in the collection will be"
            " processed independently in its own execution stream. This should"
            " be a JSONPath expression to a collection or a list of items."
        ),
    ],
    interval: Annotated[
        float | None,
        Doc("The interval in seconds between each scatter task."),
    ] = None,
) -> Any:
    raise ActionIsInterfaceError()


@registry.register(
    default_title="Gather",
    description="Collect the results of a list of execution streams into a single list.",
    display_group="Data Transform",
    namespace="core.transform",
)
def gather(
    items: Annotated[
        str,
        Doc(
            "The JSONPath expression referencing the item to gather in the current execution stream."
        ),
    ],
    drop_nulls: Annotated[
        bool,
        Doc(
            "Whether to drop null values from the final result. If True, any null values encountered during the gather operation will be omitted from the output list."
        ),
    ] = False,
    error_strategy: Annotated[
        Literal["partition", "include", "drop", "raise"],
        Doc(
            "Controls how errors are handled when gathering. "
            '"partition" puts successful results in `.result` and errors in `.error`. '
            '"include" puts errors in `.result` as JSON objects. '
            '"drop" removes errors from `.result`. '
            '"raise" fails the gather if any branch errors.'
        ),
    ] = "partition",
) -> list[Any]:
    raise ActionIsInterfaceError()


def flatten_dict(x: dict[str, Any] | list[Any], max_depth: int = 100) -> dict[str, Any]:
    """Return object with single level of keys (as jsonpath) and values."""
    return _flatten_dict(x, max_depth=max_depth)


@registry.register(
    default_title="Flatten JSON",
    description="Flatten a JSON object into a single level of fields.",
    display_group="Data Transform",
    namespace="core.transform",
)
def flatten_json(
    json: Annotated[str | dict[str, Any], Doc("JSON object to flatten.")],
) -> dict[str, Any]:
    if isinstance(json, str):
        json = orjson.loads(json)
    if not isinstance(json, dict):
        raise ValueError("json must be a JSON object or a string")
    return flatten_dict(json)


@registry.register(
    default_title="Eval JSONPaths",
    description="Eval multiple JSONPath expressions on an object.",
    display_group="Data Transform",
    namespace="core.transform",
)
def eval_jsonpaths(
    json: Annotated[
        str | dict[str, Any], Doc("JSON object to eval JSONPath expressions on.")
    ],
    jsonpaths: Annotated[list[str], Doc("JSONPath expressions to eval.")],
) -> dict[str, Any]:
    if isinstance(json, str):
        json = orjson.loads(json)
    if not isinstance(json, dict):
        raise ValueError("json must be a JSON object or a string")
    return {jsonpath: eval_jsonpath(jsonpath, json) for jsonpath in jsonpaths}
