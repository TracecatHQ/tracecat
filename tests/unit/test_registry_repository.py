from __future__ import annotations

import importlib
import importlib.machinery
import sys
from types import ModuleType
from typing import Annotated, ForwardRef, get_args, get_origin

import pytest

from tracecat.expressions.validation import TemplateValidator
from tracecat.registry.repository import attach_validators, import_and_reload


def _metadata(annotation: object) -> list[object]:
    origin = get_origin(annotation)
    if origin is Annotated:
        args = get_args(annotation)
        return list(args[1:])
    return []


def test_attach_validators_idempotent() -> None:
    def sample(x: int) -> int:  # noqa: ANN001 - test helper
        return x

    attach_validators(sample, TemplateValidator())
    attach_validators(sample, TemplateValidator())

    annotation = sample.__annotations__["x"]
    metas = _metadata(annotation)
    template_validators = [m for m in metas if isinstance(m, TemplateValidator)]

    assert len(template_validators) == 1
    if get_origin(annotation) is Annotated:
        base = get_args(annotation)[0]
        assert base in (int, "int", ForwardRef("int"))


def test_import_and_reload_falls_back_when_loader_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "tracecat._test_dummy_registry"
    dummy = ModuleType(module_name)
    dummy.__spec__ = importlib.machinery.ModuleSpec(name=module_name, loader=None)

    sentinel = ModuleType(module_name)
    sentinel.__spec__ = importlib.machinery.ModuleSpec(name=module_name, loader=None)
    calls: list[str] = []

    def fake_import(name: str) -> ModuleType:
        assert name == module_name
        calls.append(name)
        importlib.invalidate_caches()
        return sentinel

    monkeypatch.setitem(sys.modules, module_name, dummy)
    monkeypatch.setattr(
        "tracecat.registry.repository.importlib.import_module", fake_import
    )

    def boom(_: ModuleType) -> ModuleType:  # pragma: no cover - should not run
        raise AssertionError("reload should not have been called")

    monkeypatch.setattr("tracecat.registry.repository.importlib.reload", boom)

    try:
        result = import_and_reload(module_name)
        assert result is sentinel
        assert calls == [module_name]
    finally:
        sys.modules.pop(module_name, None)
