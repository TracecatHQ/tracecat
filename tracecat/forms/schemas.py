"""Form config, resolved form and API schemas."""

import uuid
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    SerializerFunctionWrapHandler,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    Tag,
    field_validator,
    model_serializer,
    model_validator,
)

from tracecat.core.schemas import Schema
from tracecat.forms.constants import MAX_CONFIG_IMAGE_BYTES, MAX_FILES_PER_FIELD
from tracecat.forms.enums import (
    FormAutofillSource,
    FormIssueCode,
    FormIssueSeverity,
    FormNoticeTone,
    FormStatus,
    FormWidget,
)
from tracecat.forms.images import decode_image_data_uri, image_data_uri_size
from tracecat.identifiers import WorkspaceID
from tracecat.identifiers.workflow import WorkflowUUID

# ----- Form config -----


class FormConfigModel(BaseModel):
    """Base for form config models.

    Unset keys are left out of dumps so the config reads back as the author
    wrote it, whatever key order JSONB returned.
    """

    model_config = ConfigDict(extra="forbid")

    @model_serializer(mode="wrap")
    def _drop_none(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return {key: value for key, value in handler(self).items() if value is not None}


class FormOption(FormConfigModel):
    """One choice of a select, multi-select or cards field."""

    value: str
    label: str | None = None
    description: str | None = None
    image: str | None = Field(
        default=None, description="Inline PNG, JPEG or WebP data URI."
    )

    @field_validator("image")
    @classmethod
    def _validate_image(cls, value: str | None) -> str | None:
        if value is not None:
            decode_image_data_uri(value)
        return value


FormScalar = StrictBool | StrictInt | StrictFloat | StrictStr


class FormVisibleIf(FormConfigModel):
    """Show a field only while another field has this value."""

    field: str
    equals: FormScalar


class FormFieldEntry(FormConfigModel):
    """Presentation of one workflow input."""

    field: str = Field(description="Name of the workflow input.")
    label: str | None = None
    help: str | None = None
    placeholder: str | None = None
    widget: FormWidget | None = None
    options: list[FormOption] | None = Field(default=None, min_length=1)
    visible_if: FormVisibleIf | None = None
    autofill: FormAutofillSource | None = None
    hidden: bool | None = None
    accept: list[str] | None = Field(
        default=None, description="File extensions or MIME types a file field takes."
    )
    max_files: int | None = Field(default=None, ge=1, le=MAX_FILES_PER_FIELD)

    @field_validator("options")
    @classmethod
    def _unique_option_values(
        cls, options: list[FormOption] | None
    ) -> list[FormOption] | None:
        if options is not None:
            values = [option.value for option in options]
            if len(values) != len(set(values)):
                raise ValueError("Option values must be unique")
        return options

    @model_validator(mode="after")
    def _forbid_presentation_keys(self) -> Self:
        if not self.hidden and self.autofill is None:
            return self
        # The dump leaves out unset keys, so what remains is presentation.
        if set_keys := sorted(self.model_dump(exclude={"field", "hidden", "autofill"})):
            raise ValueError(
                "A hidden or autofill field cannot set " + ", ".join(set_keys)
            )
        return self


class FormHeadingEntry(FormConfigModel):
    heading: str


class FormTextEntry(FormConfigModel):
    text: str = Field(description="Markdown.")


class FormNoticeEntry(FormConfigModel):
    notice: str
    tone: FormNoticeTone = Field(default=FormNoticeTone.INFO)


class FormDividerEntry(FormConfigModel):
    divider: Literal[True]


_LAYOUT_ENTRY_KEYS = ("field", "heading", "text", "notice", "divider")


def _layout_entry_tag(value: Any) -> str | None:
    """Tag a layout entry by which of its identifying keys is present."""
    if isinstance(value, dict):
        present = [key for key in _LAYOUT_ENTRY_KEYS if key in value]
    else:
        present = [key for key in _LAYOUT_ENTRY_KEYS if hasattr(value, key)]
    return present[0] if len(present) == 1 else None


FormLayoutEntry = Annotated[
    Annotated[FormFieldEntry, Tag("field")]
    | Annotated[FormHeadingEntry, Tag("heading")]
    | Annotated[FormTextEntry, Tag("text")]
    | Annotated[FormNoticeEntry, Tag("notice")]
    | Annotated[FormDividerEntry, Tag("divider")],
    Discriminator(
        _layout_entry_tag,
        custom_error_type="invalid_layout_entry",
        custom_error_message=(
            "A layout entry needs exactly one of: " + ", ".join(_LAYOUT_ENTRY_KEYS)
        ),
    ),
]


class FormConfig(FormConfigModel):
    """Presentation layer of a form. The workflow input schema supplies the rest."""

    title: str | None = Field(
        default=None, description="Defaults to the workflow title."
    )
    description: str | None = Field(
        default=None, description="Markdown. Defaults to the workflow description."
    )
    success_message: str | None = None
    layout: list[FormLayoutEntry] | None = Field(
        default=None,
        description="Ordered blocks. Inputs left out are appended in schema order.",
    )

    @model_validator(mode="after")
    def _limit_total_image_size(self) -> Self:
        total = sum(
            image_data_uri_size(option.image)
            for entry in self.layout or []
            if isinstance(entry, FormFieldEntry)
            for option in entry.options or []
            if option.image is not None
        )
        if total > MAX_CONFIG_IMAGE_BYTES:
            raise ValueError(
                f"Images in a form cannot exceed {MAX_CONFIG_IMAGE_BYTES // 1024} KB "
                "in total"
            )
        return self


# ----- Resolved form -----


class FormIssue(Schema):
    """A problem found while resolving a form config against its inputs."""

    severity: FormIssueSeverity
    code: FormIssueCode
    message: str
    loc: list[str | int] = Field(description="Path to the offending config key.")


class FormHeadingBlock(Schema):
    type: Literal["heading"]
    text: str


class FormTextBlock(Schema):
    type: Literal["text"]
    text: str = Field(description="Markdown.")


class FormNoticeBlock(Schema):
    type: Literal["notice"]
    text: str
    tone: FormNoticeTone


class FormDividerBlock(Schema):
    type: Literal["divider"]


class FormFieldOption(Schema):
    value: str
    label: str
    description: str | None = None
    image: str | None = None


class FormFieldConfig(Schema):
    """Widget-specific settings."""

    accept: list[str] | None = None
    max_files: int | None = None


class FormFieldBlock(Schema):
    """An input the requester fills in."""

    type: Literal["field"]
    name: str
    label: str
    help: str | None = None
    placeholder: str | None = None
    widget: FormWidget
    required: bool
    multiple: bool
    default: Any = None
    options: list[FormFieldOption] | None = None
    visible_if: FormVisibleIf | None = None
    config: FormFieldConfig | None = None


FormBlock = Annotated[
    FormHeadingBlock
    | FormTextBlock
    | FormNoticeBlock
    | FormDividerBlock
    | FormFieldBlock,
    Field(discriminator="type"),
]


class FormPreview(Schema):
    """What a form renders. Hidden and autofill fields never appear."""

    title: str
    description: str | None = None
    success_message: str | None = None
    definition_version: int | None = Field(
        default=None, description="Published workflow version the blocks come from."
    )
    blocks: list[FormBlock]


class FormResolved(FormPreview):
    """A saved form resolved against its workflow's published input schema."""

    id: uuid.UUID
    workspace_id: WorkspaceID
    workflow_id: WorkflowUUID
    status: FormStatus
