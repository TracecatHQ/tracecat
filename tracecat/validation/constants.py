from datetime import datetime, timedelta
from typing import Any

VALIDATION_TYPES = {
    "duration": timedelta,
    "datetime": datetime,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "any": Any,
}
