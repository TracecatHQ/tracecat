from builtins import filter as filter_
from builtins import map as map_
from typing import Annotated, Any, Literal

from typing_extensions import Doc
import hashlib
import json
import orjson
from tracecat_registry import ActionIsInterfaceError, ctx, registry
from tracecat_registry._internal.flatten import flatten_dict as _flatten_dict
from tracecat_registry._internal.exceptions import TracecatExpressionError
from tracecat_registry._internal.jsonpath import eval_jsonpath
from tracecat_registry._internal.safe_lambda import build_safe_lambda


def _compute_digests(seen: dict[tuple[Any, ...], dict[str, Any]]) -> list[str]:
    """Compute SHA256 hex digests for deduplication keys.

    Args:
        seen: Mapping of composite key tuples to their items.

    Returns:
        List of hex digests in iteration order of ``seen``.
    """
    digests: list[str] = []
    for key in seen:
        # NOTE: Must use stdlib json (not orjson) to preserve digest stability.
        # orjson produces different whitespace (no space after comma), which
        # would change SHA256 digests and invalidate existing Redis keys.
        key_str = json.dumps(key, sort_keys=True, default=str)
        digests.append(hashlib.sha256(key_str.encode()).hexdigest())
    return digests


@registry.register(
    default_title="Reshape",
    description="Define the exact scalar, object, or list output you want from workflow data.",
    display_group="Data Transform",
    namespace="core.transform",
)
def reshape(
    value: Annotated[
        Any | list[Any] | dict[str, Any],
        Doc("The value to reshape"),
    ],
) -> Any:
    """Define the exact scalar, object, or list output you want from workflow data."""
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


async def _deduplicate_persistent(
    seen: dict[tuple[Any, ...], dict[str, Any]], expire_seconds: int
) -> set[tuple[Any, ...]]:
    """Deduplicate items via the trusted internal API.

    Computes SHA256 digests for each key and delegates to the platform API
    (which owns the Redis connection) to create digest entries atomically.

    Args:
        seen: Mapping of composite key tuples to their merged items.
        expire_seconds: TTL for each dedup key.

    Returns:
        Keys whose digests were newly created (not previously seen).
    """
    digests = _compute_digests(seen)
    created = await ctx.deduplicate.aio.create_digests(digests, expire_seconds)
    return {key for key, is_new in zip(seen, created) if is_new}


@registry.register(
    default_title="Deduplicate",
    description=(
        "Deduplicate a JSON object or a list of JSON objects given a list of keys. "
        "Items with the same key values are merged, later fields overwriting earlier ones. "
        "Items missing a key are returned unchanged and never deduplicated. "
        "Returns a list of deduplicated JSON objects."
    ),
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
            "List of JSONPath keys to deduplicate by. Supports dot notation for nested keys (e.g. `['user.id']`). "
            "Items missing a key are returned unchanged and never deduplicated. "
            "An empty list deduplicates by the whole item."
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
    # Normalize input to list
    items_list = [items] if isinstance(items, dict) else items
    if not items_list:
        return []

    jsonpath_exprs = ["$." + key for key in keys]
    # Strict evaluation raises the same error for an invalid JSONPath and for
    # no match, so validate every expression up front and let a strict-mode
    # error below mean the key is missing.
    for jsonpath_expr in jsonpath_exprs:
        eval_jsonpath(jsonpath_expr, {})

    def get_nested_values(item: dict[str, Any]) -> tuple[Any, ...] | None:
        """Return the item's dedup key, or None if any key is missing."""
        if not jsonpath_exprs:
            # No keys: the whole item is the key. Nest it in a tuple, which a
            # keyed value never produces, so its digest can't match one.
            return (("item", json.dumps(item, sort_keys=True, default=str)),)
        values = []
        for jsonpath_expr in jsonpath_exprs:
            try:
                value = eval_jsonpath(jsonpath_expr, item, strict=True)
            except TracecatExpressionError:
                return None
            # Convert unhashable types to JSON strings for use as dict keys
            if isinstance(value, (list, dict)):
                value = json.dumps(value, sort_keys=True, default=str)
            values.append(value)
        return tuple(values)

    seen: dict[tuple[Any, ...], dict[str, Any]] = {}
    # Dedup keys of first occurrences, plus items missing a key, in input order
    order: list[tuple[Any, ...] | dict[str, Any]] = []

    for item in items_list:
        if not isinstance(item, dict):
            raise TypeError(
                f"Items to deduplicate must be JSON objects, got {type(item).__name__}"
            )
        key = get_nested_values(item)

        if key is None:
            # Missing a key: pass through without merging or persisting
            order.append(item)
        elif key in seen:
            # Update existing item with same key
            seen[key].update(item)
        else:
            # First time seeing this key in this call
            seen[key] = item.copy()
            order.append(key)

    if persist and seen:
        kept = await _deduplicate_persistent(seen, expire_seconds)
    else:
        kept = seen.keys()

    return [
        seen[entry] if isinstance(entry, tuple) else entry
        for entry in order
        if isinstance(entry, dict) or entry in kept
    ]


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
        Doc(
            "List of JSONPath keys to check. An item missing a key is never a duplicate. "
            "An empty list checks the whole item."
        ),
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
