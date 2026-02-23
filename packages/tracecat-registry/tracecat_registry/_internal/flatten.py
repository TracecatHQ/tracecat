from typing import Any


def flatten_dict(x: Any, max_depth: int = 100) -> dict[str, Any]:
    """Return object with single level of keys (as jsonpath) and values."""

    def _flatten(obj: Any, prefix: str = "", depth: int = 0) -> dict[str, Any]:
        if depth > max_depth:
            raise ValueError(
                f"Maximum recursion depth ({max_depth}) exceeded while flattening object"
            )

        result: dict[str, Any] = {}

        if isinstance(obj, list):
            for index, item in enumerate(obj):
                array_path = f"[{index}]"
                full_path = f"{prefix}{array_path}" if prefix else array_path

                if isinstance(item, (dict, list)):
                    result.update(_flatten(item, full_path, depth + 1))
                else:
                    result[full_path] = item
            return result

        if isinstance(obj, dict):
            for key, value in obj.items():
                full_path = f"{prefix}.{key}" if prefix else key

                if isinstance(value, (dict, list)):
                    result.update(_flatten(value, full_path, depth + 1))
                else:
                    result[full_path] = value
            return result

        return {prefix: obj} if prefix else {"": obj}

    return _flatten(x)
