"""Tests for the form config schema, widget registry, resolver and image validator."""

import base64
from typing import Any

import pytest
from pydantic import ValidationError

from tracecat.expressions.schemas import ExpectedField
from tracecat.forms.constants import MAX_CONFIG_IMAGE_BYTES, MAX_IMAGE_BYTES
from tracecat.forms.enums import (
    FormAutofillSource,
    FormInputKind,
    FormIssueCode,
    FormIssueSeverity,
    FormWidget,
)
from tracecat.forms.images import decode_image_data_uri, image_data_uri_size
from tracecat.forms.resolver import resolve_form
from tracecat.forms.schemas import (
    FormConfig,
    FormDividerEntry,
    FormFieldBlock,
    FormFieldEntry,
    FormHeadingEntry,
    FormNoticeEntry,
    FormTextEntry,
)
from tracecat.forms.types import ResolveResult
from tracecat.forms.widgets import DEFAULT_WIDGETS, WIDGETS, classify_input

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff\xe0"
WEBP_MAGIC = b"RIFF\x00\x00\x00\x00WEBP"


def _data_uri(subtype: str, data: bytes) -> str:
    return f"data:image/{subtype};base64,{base64.b64encode(data).decode()}"


def _expects(**fields: dict[str, Any]) -> dict[str, ExpectedField]:
    return {name: ExpectedField.model_validate(spec) for name, spec in fields.items()}


def _resolve(layout: list[dict[str, Any]] | None, **fields: Any) -> ResolveResult:
    config = FormConfig.model_validate({"layout": layout})
    return resolve_form(config, _expects(**fields))


def _field_blocks(result: ResolveResult) -> dict[str, FormFieldBlock]:
    return {
        block.name: block
        for block in result.blocks
        if isinstance(block, FormFieldBlock)
    }


def _codes(result: ResolveResult) -> list[FormIssueCode]:
    return [issue.code for issue in result.issues]


# ----- Image validator -----


@pytest.mark.parametrize(
    ("subtype", "magic"),
    [("png", PNG_MAGIC), ("jpeg", JPEG_MAGIC), ("webp", WEBP_MAGIC)],
)
def test_image_validator_accepts_png_jpeg_and_webp(subtype: str, magic: bytes) -> None:
    data = magic + b"\x00" * 10
    uri = _data_uri(subtype, data)

    assert decode_image_data_uri(uri) == data
    assert image_data_uri_size(uri) == len(data)


@pytest.mark.parametrize(
    "uri",
    [
        "https://example.com/laptop.png",
        "data:image/gif;base64,R0lGODlh",
        "data:image/svg+xml;base64,PHN2Zz4=",
        "data:image/png,not-base64-marked",
        "data:image/png;base64,not base64!",
        # Declared type and content disagree.
        _data_uri("png", JPEG_MAGIC + b"\x00" * 10),
        _data_uri("webp", b"RIFF\x00\x00\x00\x00WAVE"),
        _data_uri("jpeg", b""),
    ],
)
def test_image_validator_rejects_bad_images(uri: str) -> None:
    with pytest.raises(ValueError):
        decode_image_data_uri(uri)


def test_image_validator_enforces_the_per_image_limit() -> None:
    at_limit = PNG_MAGIC + b"\x00" * (MAX_IMAGE_BYTES - len(PNG_MAGIC))
    assert len(decode_image_data_uri(_data_uri("png", at_limit))) == MAX_IMAGE_BYTES

    with pytest.raises(ValueError, match="larger than"):
        decode_image_data_uri(_data_uri("png", at_limit + b"\x00"))


def test_config_rejects_an_invalid_option_image() -> None:
    with pytest.raises(ValidationError):
        FormConfig.model_validate(
            {
                "layout": [
                    {
                        "field": "model",
                        "widget": "cards",
                        "options": [
                            {"value": "mba", "image": "https://example.com/a.png"}
                        ],
                    }
                ]
            }
        )


def test_config_enforces_the_total_image_limit() -> None:
    image = _data_uri("png", PNG_MAGIC + b"\x00" * (MAX_IMAGE_BYTES - len(PNG_MAGIC)))
    count = MAX_CONFIG_IMAGE_BYTES // MAX_IMAGE_BYTES

    def config(images: int) -> dict[str, Any]:
        return {
            "layout": [
                {
                    "field": "model",
                    "widget": "cards",
                    "options": [
                        {"value": f"option-{index}", "image": image}
                        for index in range(images)
                    ],
                }
            ]
        }

    FormConfig.model_validate(config(count))
    with pytest.raises(ValidationError, match="in total"):
        FormConfig.model_validate(config(count + 1))


# ----- Config schema -----


def test_layout_entries_are_discriminated_by_their_key() -> None:
    config = FormConfig.model_validate(
        {
            "layout": [
                {"notice": "Reviewed within 2 business days.", "tone": "warning"},
                {"field": "model"},
                {"heading": "Delivery"},
                {"text": "Read the **policy** first."},
                {"divider": True},
            ]
        }
    )

    assert [type(entry) for entry in config.layout or []] == [
        FormNoticeEntry,
        FormFieldEntry,
        FormHeadingEntry,
        FormTextEntry,
        FormDividerEntry,
    ]


@pytest.mark.parametrize(
    "entry",
    [
        {},
        {"label": "No identifying key"},
        {"field": "model", "heading": "Two identifying keys"},
        {"divider": False},
        {"heading": "Delivery", "unknown": 1},
        {"field": "model", "widget": "slider"},
        {"field": "model", "max_files": 11},
        {"field": "model", "options": []},
        {"field": "model", "options": [{"value": "a"}, {"value": "a"}]},
        {"field": "model", "autofill": "requester.phone"},
        {"field": "model", "visible_if": {"field": "other", "equals": ["a"]}},
    ],
)
def test_invalid_layout_entries_are_rejected(entry: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        FormConfig.model_validate({"layout": [entry]})


def test_config_rejects_unknown_top_level_keys() -> None:
    with pytest.raises(ValidationError):
        FormConfig.model_validate({"status": "online"})


@pytest.mark.parametrize(
    "entry",
    [
        {"field": "requester", "hidden": True, "label": "Requester"},
        {"field": "requester", "autofill": "requester", "widget": "user"},
        {"field": "requester", "autofill": "requester", "help": "Filled in"},
        {
            "field": "requester",
            "hidden": True,
            "visible_if": {"field": "other", "equals": True},
        },
    ],
)
def test_hidden_and_autofill_forbid_presentation_keys(entry: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="cannot set"):
        FormConfig.model_validate({"layout": [entry]})


def test_config_dump_keeps_only_what_the_author_wrote() -> None:
    # JSONB returns the keys of each entry in its own order.
    stored = {
        "layout": [
            {"widget": "cards", "label": "Laptop", "field": "model"},
            {"tone": "info", "notice": "Reviewed within 2 business days."},
        ],
        "title": "Laptop request",
    }

    assert FormConfig.model_validate(stored).model_dump(mode="json") == {
        "title": "Laptop request",
        "layout": [
            {"field": "model", "label": "Laptop", "widget": "cards"},
            {"notice": "Reviewed within 2 business days.", "tone": "info"},
        ],
    }
    dumped_entry = FormConfig.model_validate(stored).model_dump()["layout"][0]
    assert list(dumped_entry) == ["field", "label", "widget"]


# ----- Widget registry -----

KIND_BY_TYPE = {
    "str": FormInputKind.STR,
    "str | None": FormInputKind.STR,
    "int": FormInputKind.INT,
    "float": FormInputKind.FLOAT,
    "bool": FormInputKind.BOOL,
    "datetime": FormInputKind.DATETIME,
    'enum["low", "high"]': FormInputKind.ENUM,
    'enum["low", "high"] | None': FormInputKind.ENUM,
    "list[str]": FormInputKind.LIST_STR,
    'list[enum["low", "high"]]': FormInputKind.LIST_ENUM,
    "dict": FormInputKind.DICT,
    "dict[str, Any]": FormInputKind.DICT,
    "list[dict]": FormInputKind.LIST_DICT,
    "any": FormInputKind.ANY,
    "Any": FormInputKind.ANY,
    "duration": FormInputKind.UNSUPPORTED,
    "None": FormInputKind.UNSUPPORTED,
    "list[int]": FormInputKind.UNSUPPORTED,
    "str | int": FormInputKind.UNSUPPORTED,
    "$Reference": FormInputKind.UNSUPPORTED,
    "not a type": FormInputKind.UNSUPPORTED,
}


@pytest.mark.parametrize(("type_string", "kind"), KIND_BY_TYPE.items())
def test_classify_input(type_string: str, kind: FormInputKind) -> None:
    assert classify_input(type_string, "field").kind is kind


def test_classify_input_returns_enum_values() -> None:
    assert classify_input('enum["low", "high"]', "field").enum_values == (
        "low",
        "high",
    )
    assert classify_input('list[enum["low", "high"]]', "field").enum_values == (
        "low",
        "high",
    )


def test_registry_has_one_entry_per_widget() -> None:
    assert set(WIDGETS) == set(FormWidget)


def test_registry_matches_the_widget_table() -> None:
    k = FormInputKind
    accepts = {widget: set(spec.accepts) for widget, spec in WIDGETS.items()}

    assert accepts == {
        FormWidget.TEXT: {k.STR},
        FormWidget.LONG_TEXT: {k.STR},
        FormWidget.NUMBER: {k.INT, k.FLOAT},
        FormWidget.TOGGLE: {k.BOOL},
        FormWidget.SELECT: {k.ENUM, k.STR},
        FormWidget.MULTI_SELECT: {k.LIST_ENUM, k.LIST_STR},
        FormWidget.CARDS: {k.ENUM, k.STR, k.LIST_ENUM, k.LIST_STR},
        FormWidget.TAGS: {k.LIST_STR},
        FormWidget.DATE: {k.STR, k.DATETIME},
        FormWidget.DATETIME: {k.DATETIME, k.STR},
        FormWidget.USER: {k.DICT, k.LIST_DICT},
        FormWidget.FILE: {k.DICT, k.LIST_DICT},
    }
    assert DEFAULT_WIDGETS == {
        k.STR: FormWidget.TEXT,
        k.INT: FormWidget.NUMBER,
        k.FLOAT: FormWidget.NUMBER,
        k.BOOL: FormWidget.TOGGLE,
        k.ENUM: FormWidget.SELECT,
        k.LIST_ENUM: FormWidget.MULTI_SELECT,
        k.LIST_STR: FormWidget.TAGS,
        k.DATETIME: FormWidget.DATETIME,
    }


@pytest.mark.parametrize("kind", FormInputKind)
def test_every_input_kind_gets_its_default_widget_or_none(kind: FormInputKind) -> None:
    type_string = next(t for t, k in KIND_BY_TYPE.items() if k is kind)
    default = DEFAULT_WIDGETS.get(kind)
    if default is not None:
        assert kind in WIDGETS[default].accepts

    required = _resolve(None, value={"type": type_string})
    optional = _resolve(None, value={"type": type_string, "default": None})

    if default is None:
        assert _codes(required) == [FormIssueCode.NO_WIDGET_FOR_TYPE]
        assert _codes(optional) == [FormIssueCode.UNRENDERED_OPTIONAL_INPUT]
        assert not required.blocks and not optional.blocks
    else:
        assert not required.issues
        assert _field_blocks(required)["value"].widget is default
        assert _field_blocks(optional)["value"].required is False


@pytest.mark.parametrize("widget", FormWidget)
@pytest.mark.parametrize("kind", FormInputKind)
def test_widget_compatibility_for_every_input_kind(
    widget: FormWidget, kind: FormInputKind
) -> None:
    type_string = next(t for t, k in KIND_BY_TYPE.items() if k is kind)
    entry: dict[str, Any] = {"field": "value", "widget": widget}
    if WIDGETS[widget].options:
        entry["options"] = [{"value": "low"}]

    result = _resolve([entry], value={"type": type_string})

    if kind in WIDGETS[widget].accepts:
        assert not result.has_errors
        assert _field_blocks(result)["value"].widget is widget
    else:
        assert _codes(result) == [FormIssueCode.INCOMPATIBLE_WIDGET]
        assert result.issues[0].loc == ["layout", 0, "widget"]
        assert not result.blocks


# ----- Resolver -----

LAPTOP_EXPECTS: dict[str, dict[str, Any]] = {
    "model": {"type": 'enum["mba", "mbp"]', "description": "Laptop model"},
    "justification": {"type": "str"},
    "for_someone_else": {"type": "bool", "default": False},
    "recipient": {"type": "dict | None", "default": None},
    "quote": {"type": "list[dict]", "default": []},
    "requester": {"type": "dict"},
}

LAPTOP_LAYOUT: list[dict[str, Any]] = [
    {"notice": "Requests are reviewed within 2 business days."},
    {
        "field": "model",
        "label": "Choose your laptop",
        "widget": "cards",
        "options": [
            {"value": "mbp", "label": "MacBook Pro 14"},
        ],
    },
    {"field": "justification", "widget": "long_text"},
    {"heading": "Delivery"},
    {"field": "for_someone_else"},
    {
        "field": "recipient",
        "widget": "user",
        "visible_if": {"field": "for_someone_else", "equals": True},
    },
    {"field": "quote", "widget": "file", "accept": [".pdf", "image/*"], "max_files": 2},
    {"field": "requester", "autofill": "requester"},
]


def test_resolves_the_spec_example() -> None:
    result = _resolve(LAPTOP_LAYOUT, **LAPTOP_EXPECTS)

    assert not result.issues
    assert [block.type for block in result.blocks] == [
        "notice",
        "field",
        "field",
        "heading",
        "field",
        "field",
        "field",
    ]
    assert result.autofill == {"requester": FormAutofillSource.REQUESTER}
    assert result.hidden == []

    fields = _field_blocks(result)
    assert list(fields) == [
        "model",
        "justification",
        "for_someone_else",
        "recipient",
        "quote",
    ]
    model = fields["model"]
    assert model.label == "Choose your laptop"
    assert model.help == "Laptop model"
    assert model.required is True
    assert model.multiple is False
    # Decorated options come first, the rest of the enum follows.
    assert [(o.value, o.label) for o in model.options or []] == [
        ("mbp", "MacBook Pro 14"),
        ("mba", "mba"),
    ]
    assert fields["for_someone_else"].label == "For someone else"
    assert fields["for_someone_else"].widget is FormWidget.TOGGLE
    assert fields["for_someone_else"].default is False
    assert fields["recipient"].visible_if is not None
    assert fields["recipient"].required is False
    quote = fields["quote"]
    assert quote.multiple is True
    assert quote.config is not None
    assert quote.config.accept == [".pdf", "image/*"]
    assert quote.config.max_files == 2


def test_no_layout_renders_every_input_in_schema_order() -> None:
    result = _resolve(
        None,
        zeta={"type": "str"},
        mm={"type": "int", "default": 1},
        a={"type": "bool", "default": False},
    )

    assert not result.issues
    assert list(_field_blocks(result)) == ["zeta", "mm", "a"]


def test_inputs_missing_from_the_layout_are_appended() -> None:
    result = _resolve(
        [{"field": "b"}],
        a={"type": "str"},
        b={"type": "str"},
        c={"type": "str"},
    )

    assert list(_field_blocks(result)) == ["b", "a", "c"]


def test_hidden_field_is_not_rendered() -> None:
    result = _resolve(
        [{"field": "priority", "hidden": True}],
        priority={"type": "str", "default": "low"},
    )

    assert not result.issues
    assert result.hidden == ["priority"]
    assert not result.blocks


def test_str_options_define_the_allowed_values() -> None:
    result = _resolve(
        [
            {
                "field": "size",
                "widget": "select",
                "options": [{"value": "s"}, {"value": "m", "label": "Medium"}],
            }
        ],
        size={"type": "str"},
    )

    assert not result.issues
    options = _field_blocks(result)["size"].options or []
    assert [(o.value, o.label) for o in options] == [("s", "s"), ("m", "Medium")]


def test_single_file_field_takes_one_file() -> None:
    result = _resolve([{"field": "quote", "widget": "file"}], quote={"type": "dict"})

    config = _field_blocks(result)["quote"].config
    assert config is not None
    assert config.max_files == 1


ISSUE_CASES: list[tuple[FormIssueCode, list[dict[str, Any]], dict[str, Any], list]] = [
    (
        FormIssueCode.UNKNOWN_FIELD,
        [{"field": "missing"}],
        {"name": {"type": "str"}},
        ["layout", 0, "field"],
    ),
    (
        FormIssueCode.DUPLICATE_FIELD,
        [{"field": "name"}, {"field": "name"}],
        {"name": {"type": "str"}},
        ["layout", 1, "field"],
    ),
    (
        FormIssueCode.INCOMPATIBLE_WIDGET,
        [{"field": "name", "widget": "toggle"}],
        {"name": {"type": "str"}},
        ["layout", 0, "widget"],
    ),
    (
        FormIssueCode.NO_WIDGET_FOR_TYPE,
        [{"field": "wait"}],
        {"wait": {"type": "duration"}},
        ["layout", 0],
    ),
    (
        FormIssueCode.UNSUPPORTED_WIDGET_OPTION,
        [{"field": "name", "accept": [".pdf"]}],
        {"name": {"type": "str"}},
        ["layout", 0, "accept"],
    ),
    (
        FormIssueCode.UNSUPPORTED_WIDGET_OPTION,
        [{"field": "agree", "placeholder": "Yes"}],
        {"agree": {"type": "bool"}},
        ["layout", 0, "placeholder"],
    ),
    (
        FormIssueCode.UNSUPPORTED_WIDGET_OPTION,
        [{"field": "quote", "widget": "file", "max_files": 2}],
        {"quote": {"type": "dict"}},
        ["layout", 0, "max_files"],
    ),
    (
        FormIssueCode.OPTIONS_REQUIRED,
        [{"field": "size", "widget": "select"}],
        {"size": {"type": "str"}},
        ["layout", 0],
    ),
    (
        FormIssueCode.OPTIONS_NOT_SUPPORTED,
        [{"field": "name", "options": [{"value": "a"}]}],
        {"name": {"type": "str"}},
        ["layout", 0, "options"],
    ),
    (
        FormIssueCode.OPTION_NOT_IN_ENUM,
        [{"field": "model", "options": [{"value": "mba"}, {"value": "dell"}]}],
        {"model": {"type": 'enum["mba", "mbp"]'}},
        ["layout", 0, "options", 1, "value"],
    ),
    (
        FormIssueCode.CONDITIONAL_FIELD_REQUIRED,
        [{"field": "name", "hidden": True}],
        {"name": {"type": "str"}},
        ["layout", 0, "hidden"],
    ),
    (
        FormIssueCode.CONDITIONAL_FIELD_REQUIRED,
        [
            {"field": "other"},
            {"field": "name", "visible_if": {"field": "other", "equals": True}},
        ],
        {"other": {"type": "bool"}, "name": {"type": "str"}},
        ["layout", 1, "visible_if"],
    ),
    (
        FormIssueCode.VISIBLE_IF_UNKNOWN_FIELD,
        [{"field": "name", "visible_if": {"field": "missing", "equals": True}}],
        {"name": {"type": "str", "default": ""}},
        ["layout", 0, "visible_if", "field"],
    ),
    (
        # The controller comes later in the layout.
        FormIssueCode.VISIBLE_IF_INVALID_CONTROLLER,
        [
            {"field": "name", "visible_if": {"field": "other", "equals": True}},
            {"field": "other"},
        ],
        {"other": {"type": "bool"}, "name": {"type": "str", "default": ""}},
        ["layout", 0, "visible_if", "field"],
    ),
    (
        # The controller is itself conditional.
        FormIssueCode.VISIBLE_IF_INVALID_CONTROLLER,
        [
            {"field": "first"},
            {"field": "second", "visible_if": {"field": "first", "equals": True}},
            {"field": "third", "visible_if": {"field": "second", "equals": True}},
        ],
        {
            "first": {"type": "bool"},
            "second": {"type": "bool", "default": False},
            "third": {"type": "str", "default": ""},
        },
        ["layout", 2, "visible_if", "field"],
    ),
    (
        # The controller is not a scalar.
        FormIssueCode.VISIBLE_IF_INVALID_CONTROLLER,
        [
            {"field": "tags"},
            {"field": "name", "visible_if": {"field": "tags", "equals": "a"}},
        ],
        {"tags": {"type": "list[str]"}, "name": {"type": "str", "default": ""}},
        ["layout", 1, "visible_if", "field"],
    ),
    (
        FormIssueCode.VISIBLE_IF_INVALID_VALUE,
        [
            {"field": "other"},
            {"field": "name", "visible_if": {"field": "other", "equals": "yes"}},
        ],
        {"other": {"type": "bool"}, "name": {"type": "str", "default": ""}},
        ["layout", 1, "visible_if", "equals"],
    ),
    (
        FormIssueCode.VISIBLE_IF_INVALID_VALUE,
        [
            {"field": "model"},
            {"field": "name", "visible_if": {"field": "model", "equals": "dell"}},
        ],
        {
            "model": {"type": 'enum["mba", "mbp"]'},
            "name": {"type": "str", "default": ""},
        },
        ["layout", 1, "visible_if", "equals"],
    ),
    (
        FormIssueCode.AUTOFILL_TYPE_MISMATCH,
        [{"field": "requester", "autofill": "requester"}],
        {"requester": {"type": "str"}},
        ["layout", 0, "autofill"],
    ),
    (
        FormIssueCode.AUTOFILL_TYPE_MISMATCH,
        [{"field": "email", "autofill": "requester.email"}],
        {"email": {"type": "dict"}},
        ["layout", 0, "autofill"],
    ),
]


@pytest.mark.parametrize(("code", "layout", "expects", "loc"), ISSUE_CASES)
def test_resolver_error_codes(
    code: FormIssueCode,
    layout: list[dict[str, Any]],
    expects: dict[str, Any],
    loc: list[str | int],
) -> None:
    result = _resolve(layout, **expects)

    assert result.has_errors
    assert [(issue.code, issue.loc) for issue in result.issues] == [(code, loc)]
    assert result.issues[0].severity is FormIssueSeverity.ERROR


def test_unrendered_optional_input_is_a_warning() -> None:
    result = _resolve(None, wait={"type": "duration", "default": "PT1H"})

    assert not result.has_errors
    assert [(issue.code, issue.severity) for issue in result.issues] == [
        (FormIssueCode.UNRENDERED_OPTIONAL_INPUT, FormIssueSeverity.WARNING)
    ]


def test_every_issue_code_is_covered() -> None:
    covered = {case[0] for case in ISSUE_CASES} | {
        FormIssueCode.UNRENDERED_OPTIONAL_INPUT
    }

    assert covered == set(FormIssueCode)


@pytest.mark.parametrize(
    ("source", "type_string"),
    [
        (FormAutofillSource.REQUESTER, "dict"),
        (FormAutofillSource.REQUESTER, "any"),
        (FormAutofillSource.REQUESTER_EMAIL, "str"),
        (FormAutofillSource.REQUESTER_USER_ID, "str | None"),
        (FormAutofillSource.SUBMISSION_ID, "any"),
    ],
)
def test_autofill_fills_inputs_without_a_widget(
    source: FormAutofillSource, type_string: str
) -> None:
    result = _resolve(
        [{"field": "value", "autofill": source}], value={"type": type_string}
    )

    assert not result.issues
    assert result.autofill == {"value": source}
    assert not result.blocks
