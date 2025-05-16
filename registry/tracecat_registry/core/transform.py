from builtins import filter as filter_
from builtins import map as map_
from typing import Annotated, Any, Literal, overload
import collections
import re

from tracecat.expressions.common import build_safe_lambda, eval_jsonpath
from typing_extensions import Doc

from tracecat_registry import registry


@overload
def _find_none_values(obj: Any, fast_check: Literal[True]) -> bool: ...


@overload
def _find_none_values(obj: Any, fast_check: Literal[False] = False) -> list[str]: ...


def _find_none_values(obj: Any, fast_check: bool = False) -> bool | list[str]:
    """Find all None values in nested structures and return their dot paths.

    For field names containing dots, spaces, or special characters,
    the path will use quotes: a."b.c".d, x."special key".z

    Args:
        obj: Object to check for None values
        fast_check: If True, returns a boolean immediately when a None is found
                    instead of collecting all paths (faster)

    Returns:
        If fast_check=True: Boolean indicating if any None values were found
        If fast_check=False: List of dot notation paths to null values
    """
    # Regex pattern for characters that require quoting in dot notation
    NEEDS_QUOTES_PATTERN = re.compile(r"[^a-zA-Z0-9_]")

    def needs_quotes(key: str) -> bool:
        """Check if a key needs to be quoted in dot notation."""
        return bool(NEEDS_QUOTES_PATTERN.search(key))

    if obj is None:
        return True if fast_check else [""]

    # Use collections.deque for C-optimized queue operations
    if fast_check:
        queue = collections.deque([obj])

        while queue:
            current = queue.popleft()

            if isinstance(current, dict):
                # Use items() for C-optimized iteration
                for _, value in current.items():
                    if value is None:
                        return True
                    elif isinstance(value, (dict, list)):
                        queue.append(value)

            elif isinstance(current, list):
                # Use extend for C-optimized batch append
                queue.extend(item for item in current if isinstance(item, (dict, list)))
                # Check for None in a C-optimized way
                if any(item is None for item in current):
                    return True

        return False
    else:
        queue = collections.deque([("", obj)])
        null_paths = []

        while queue:
            path, current = queue.popleft()

            if isinstance(current, dict):
                # Use items() for C-optimized iteration
                for key, value in current.items():
                    if isinstance(key, str):
                        # Quote keys with special characters (not just dots)
                        if needs_quotes(key):
                            # Escape any quotes in the key
                            escaped_key = key.replace('"', '\\"')
                            key_repr = f'"{escaped_key}"'
                        else:
                            key_repr = key
                    else:
                        # Handle non-string keys
                        key_repr = str(key)

                    new_path = f"{path}.{key_repr}" if path else key_repr

                    if value is None:
                        null_paths.append(new_path)
                    elif isinstance(value, (dict, list)):
                        queue.append((new_path, value))

            elif isinstance(current, list):
                for i, item in enumerate(current):
                    new_path = f"{path}[{i}]"
                    if item is None:
                        null_paths.append(new_path)
                    elif isinstance(item, (dict, list)):
                        queue.append((new_path, item))

        return null_paths


def _drop_none_values(obj: Any) -> Any:
    """Remove None values from nested objects and lists."""
    if isinstance(obj, dict):
        return {k: _drop_none_values(v) for k, v in obj.items() if v is not None}
    elif isinstance(obj, list):
        return [_drop_none_values(item) for item in obj if item is not None]
    return obj


@registry.register(
    default_title="Reshape",
    description="Reshape inputs into outputs.",
    display_group="Data Transform",
    namespace="core.transform",
)
def reshape(
    value: Annotated[
        Any | list[Any] | dict[str, Any],
        Doc("The value to reshape"),
    ],
    on_null: Annotated[
        Literal["drop", "raise", "check", "ignore"],
        Doc(
            "If `drop`, silently removes all null values from the output. "
            "If `raise`, raises an error if any null value in output. "
            "If `check`, raises an informative error with dot paths to null value fields. "
            "Defaults to `ignore`, which keeps null values in output. "
        ),
    ] = "ignore",
) -> Any:
    if on_null == "ignore":
        return value

    if on_null == "check":
        null_paths = _find_none_values(value, fast_check=False)
        if null_paths:  # Now type checker knows this is always a list
            null_paths_str = "\n - ".join(null_paths)
            msg = (
                "Null values encountered in output. "
                f"Found null values in the following fields:\n\n - {null_paths_str}"
            )
            raise ValueError(msg)

    if on_null == "raise":
        has_nulls = _find_none_values(value, fast_check=True)
        if has_nulls:
            msg = "Null values encountered in output."
            raise ValueError(msg)

    if on_null == "drop":
        return _drop_none_values(value)

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


@registry.register(
    default_title="Deduplicate",
    description="Deduplicate list of JSON objects given a list of keys.",
    display_group="Data Transform",
    namespace="core.transform",
)
def deduplicate(
    items: Annotated[list[dict[str, Any]], Doc("List of JSON objects to deduplicate.")],
    keys: Annotated[
        list[str],
        Doc(
            "List of keys to deduplicate by. Supports dot notation for nested keys (e.g. `['user.id']`)."
        ),
    ],
) -> list[dict[str, Any]]:
    if not items:
        return []

    def get_nested_values(item: dict[str, Any], keys: list[str]) -> tuple[Any, ...]:
        values = []
        for key in keys:
            # Convert dot notation to jsonpath format
            jsonpath_expr = "$." + key
            value = eval_jsonpath(jsonpath_expr, item, strict=True)
            values.append(value)
        return tuple(values)

    seen = {}
    for item in items:
        key = get_nested_values(item, keys)
        if key in seen:
            seen[key].update(item)
        else:
            seen[key] = item.copy()

    return list(seen.values())


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
    default_title="Compact",
    description="Remove all null or empty string values from a list.",
    display_group="Data Transform",
    namespace="core.transform",
)
def compact(
    items: Annotated[list[Any], Doc("List of items to compact.")],
) -> list[Any]:
    return [item for item in items if item is not None and item != ""]
