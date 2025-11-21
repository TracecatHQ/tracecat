from __future__ import annotations

import uuid

from pydantic import BaseModel, TypeAdapter

from tracecat.exceptions import RegistryError
from tracecat.registry.actions.schemas import BoundRegistryAction
from tracecat.registry.index import RegistryIndex


class _Args(BaseModel):
    foo: int


def _udf(foo: int) -> str:  # pragma: no cover - simple helper
    return str(foo)


def build_bound_action(**kwargs) -> BoundRegistryAction:
    return BoundRegistryAction(
        fn=_udf,
        template_action=None,
        name="sample",
        description="sample action",
        namespace="tests",
        type="udf",
        origin="builtin",
        secrets=None,
        args_cls=_Args,
        args_docs={"foo": "number"},
        rtype_cls=str,
        rtype_adapter=TypeAdapter(str),
        default_title="Sample",
        display_group="Tests",
        doc_url=None,
        author=None,
        deprecated=None,
        include_in_schema=False,
        requires_approval=True,
        **kwargs,
    )


def test_registry_index_add_and_lookup():
    repository_id = uuid.uuid4()
    bound = build_bound_action()
    index = RegistryIndex()

    index.add(bound, registry_version="abc", repository_id=repository_id)

    spec = index.get_spec(bound.action)
    assert spec.repository_id == repository_id
    assert spec.registry_version == "abc"
    assert spec.options.include_in_schema is False
    assert spec.options.requires_approval is True

    loader = index.get_loader(bound.action)
    assert loader.fn is _udf


def test_registry_index_duplicate_detection():
    bound = build_bound_action()
    index = RegistryIndex.from_store({bound.action: bound})

    try:
        index.add(build_bound_action())
    except RegistryError:
        pass
    else:  # pragma: no cover - ensure failure surfaces
        raise AssertionError("RegistryError not raised on duplicate action")
