"""Validation tests for variable request schemas."""

import pytest
from pydantic import ValidationError

from tracecat.variables.schemas import VariableCreate, VariableUpdate


@pytest.mark.parametrize("name", ["TF-Probe Bad!", "Upper", "has space", "a.b"])
def test_variable_create_rejects_unanchored_names(name: str) -> None:
    with pytest.raises(ValidationError):
        VariableCreate(name=name, values={"key": "value"})


def test_variable_create_accepts_valid_name() -> None:
    variable = VariableCreate(name="tf_probe_1", values={"key": "value"})
    assert variable.name == "tf_probe_1"


def test_variable_update_rejects_null_values() -> None:
    with pytest.raises(ValidationError, match="values cannot be null"):
        VariableUpdate.model_validate({"values": None})


def test_variable_update_allows_omitting_values() -> None:
    update = VariableUpdate.model_validate({"description": "updated"})
    assert update.values is None
    assert "values" not in update.model_fields_set
