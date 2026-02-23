from typing import Any, cast

import pytest
from tracecat_registry.core.ai import select_field, select_fields


@pytest.mark.anyio
async def test_select_field_unsupported_algorithm_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unsupported algorithm: invalid"):
        await select_field(
            json={"name": "tracecat"},
            criteria_prompt="Pick the name field.",
            algorithm=cast(Any, "invalid"),
        )


@pytest.mark.anyio
async def test_select_fields_unsupported_algorithm_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unsupported algorithm: invalid"):
        await select_fields(
            json={"name": "tracecat"},
            criteria_prompt="Pick the name field.",
            algorithm=cast(Any, "invalid"),
        )
