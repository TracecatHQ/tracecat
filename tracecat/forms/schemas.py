"""Form config, resolved form and API schemas."""

import uuid
from datetime import datetime
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
from tracecat.forms.constants import (
    MAX_ACCEPT_ENTRIES,
    MAX_CONFIG_IMAGE_BYTES,
    MAX_FIELD_OPTIONS,
    MAX_FILES_PER_FIELD,
    MAX_LAYOUT_ENTRIES,
    MAX_LONG_TEXT_LENGTH,
    MAX_SHORT_TEXT_LENGTH,
)
from tracecat.forms.enums import (
    FormAutofillSource,
    FormErrorCode,
    FormEventType,
    FormFieldErrorCode,
    FormIssueCode,
    FormIssueSeverity,
    FormNoticeTone,
    FormStatus,
    FormWidget,
)
from tracecat.forms.images import decode_image_data_uri, image_data_uri_size
from tracecat.identifiers import WorkspaceID
from tracecat.identifiers.workflow import AnyWorkflowID, WorkflowUUID
from tracecat.workflow.executions.enums import WorkflowExecutionStatusLiteral

# ----- Form config -----


class FormConfigModel(BaseModel):
    """Base for form config models.

    Unset keys are left out of dumps so the config reads back as the author
    wrote it, whatever key order JSONB returned.
    """

    model_config = ConfigDict(extra="forbid")

    # No return annotation on purpose: pydantic would publish it as the
    # serialization schema and the OpenAPI output type would lose its fields.
    @model_serializer(mode="wrap")
    def _drop_none(self, handler: SerializerFunctionWrapHandler):
        dumped: dict[str, Any] = handler(self)
        return {key: value for key, value in dumped.items() if value is not None}


ShortText = Annotated[str, Field(max_length=MAX_SHORT_TEXT_LENGTH)]
LongText = Annotated[str, Field(max_length=MAX_LONG_TEXT_LENGTH)]


class FormOption(FormConfigModel):
    """One choice of a select, multi-select or cards field."""

    value: ShortText
    label: ShortText | None = None
    description: LongText | None = None
    image: str | None = Field(
        default=None, description="Inline PNG, JPEG or WebP data URI."
    )

    @field_validator("image")
    @classmethod
    def _validate_image(cls, value: str | None) -> str | None:
        if value is not None:
            decode_image_data_uri(value)
        return value


FormScalar = (
    StrictBool
    | StrictInt
    | StrictFloat
    | Annotated[StrictStr, Field(max_length=MAX_SHORT_TEXT_LENGTH)]
)


class FormVisibleIf(FormConfigModel):
    """Show a field only while another field has this value."""

    field: ShortText
    equals: FormScalar


class FormFieldEntry(FormConfigModel):
    """Presentation of one workflow input."""

    field: str = Field(
        max_length=MAX_SHORT_TEXT_LENGTH, description="Name of the workflow input."
    )
    label: ShortText | None = None
    help: LongText | None = None
    placeholder: ShortText | None = None
    widget: FormWidget | None = None
    options: list[FormOption] | None = Field(
        default=None, min_length=1, max_length=MAX_FIELD_OPTIONS
    )
    visible_if: FormVisibleIf | None = None
    autofill: FormAutofillSource | None = None
    hidden: bool | None = None
    accept: list[ShortText] | None = Field(
        default=None,
        max_length=MAX_ACCEPT_ENTRIES,
        description="File extensions or MIME types a file field takes.",
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
    heading: ShortText


class FormTextEntry(FormConfigModel):
    text: str = Field(max_length=MAX_LONG_TEXT_LENGTH, description="Markdown.")


class FormNoticeEntry(FormConfigModel):
    notice: LongText
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
        default=None,
        max_length=MAX_SHORT_TEXT_LENGTH,
        description="Defaults to the workflow title.",
    )
    description: str | None = Field(
        default=None,
        max_length=MAX_LONG_TEXT_LENGTH,
        description="Markdown. Defaults to the workflow description.",
    )
    success_message: LongText | None = None
    layout: list[FormLayoutEntry] | None = Field(
        default=None,
        max_length=MAX_LAYOUT_ENTRIES,
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
    max_file_size: int | None = Field(
        default=None, description="Largest file the field takes, in bytes."
    )


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


# ----- Builder API -----


class FormCreate(Schema):
    workflow_id: AnyWorkflowID
    status: FormStatus = Field(default=FormStatus.OFFLINE)
    config: FormConfig | None = None


class FormUpdate(Schema):
    status: FormStatus | None = None
    config: FormConfig | None = Field(
        default=None, description="Replaces the whole config."
    )


class FormValidateRequest(Schema):
    workflow_id: AnyWorkflowID
    config: FormConfig


class FormValidateResponse(Schema):
    valid: bool
    issues: list[FormIssue]
    preview: FormPreview


class FormCounts(Schema):
    views: int
    submissions: int


class FormReadMinimal(Schema):
    id: uuid.UUID
    workflow_id: WorkflowUUID | None = Field(
        default=None,
        description="Null without form:update or form:submission:read.",
    )
    workflow_title: str | None = Field(
        default=None,
        description="Null without form:update or form:submission:read.",
    )
    status: FormStatus
    title: str
    description: str | None = None
    submission_count: int | None = Field(
        default=None, description="Null without form:submission:read."
    )
    last_submitted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class FormRead(Schema):
    id: uuid.UUID
    workspace_id: WorkspaceID
    workflow_id: WorkflowUUID
    workflow_title: str
    workflow_version: int | None = Field(
        default=None,
        description="Published workflow version the inputs come from, if any.",
    )
    status: FormStatus
    config: FormConfig
    issues: list[FormIssue]
    url: str
    counts: FormCounts | None = Field(
        default=None, description="Null without form:submission:read."
    )
    created_at: datetime
    updated_at: datetime


class FormActorRead(Schema):
    """Who viewed or submitted a form."""

    type: Literal["user", "service_account"]
    id: uuid.UUID
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    name: str | None = Field(default=None, description="Service account name.")


class FormEventRead(Schema):
    id: uuid.UUID
    type: FormEventType
    actor: FormActorRead | None = None
    submission_id: uuid.UUID | None = None
    reference: str | None = None
    created_at: datetime


class FormUserRead(Schema):
    """A choice of the ``user`` widget, and what a chosen user expands to."""

    user_id: uuid.UUID
    email: str
    first_name: str | None = None
    last_name: str | None = None


# ----- Submissions -----


class FormSubmissionCreate(Schema):
    values: dict[str, Any] = Field(
        default_factory=dict, description="Answers keyed by field name."
    )
    definition_version: int | None = Field(
        default=None,
        description=(
            "The definition_version of the form the requester filled in. "
            "If set and stale, the submission is rejected with form_changed."
        ),
    )


class FormSubmissionReceipt(Schema):
    """What a requester gets back. Carries no run fields."""

    id: uuid.UUID
    reference: str
    submitted_at: datetime
    success_message: str | None = None


class FormSubmissionRead(Schema):
    id: uuid.UUID
    form_id: uuid.UUID
    reference: str
    answers: dict[str, Any]
    submitted_by: FormActorRead | None = None
    definition_version: int | None = Field(
        default=None, description="Null without form:submission:read."
    )
    wf_exec_id: str | None = Field(
        default=None, description="Null without form:submission:read."
    )
    run_status: WorkflowExecutionStatusLiteral | None = Field(
        default=None,
        description="Null without form:submission:read, or if it is not known.",
    )
    submitted_at: datetime


# ----- Files -----


class FormFileRead(Schema):
    """An uploaded file. Also what a file ID expands to in the answers.

    Carries no storage key and no URL.
    """

    id: uuid.UUID
    name: str
    content_type: str
    size: int
    sha256: str


class FormFileContent(FormFileRead):
    """A file with its bytes, as a workflow run downloads it."""

    content_base64: str


class FormFileDownload(Schema):
    download_url: str = Field(description="Short-lived URL that forces a download.")
    file_name: str
    content_type: str


# ----- Requester API -----


class FormIntakeLocation(Schema):
    """Where a form lives, for a requester who only has its ID."""

    form_id: uuid.UUID
    workspace_id: WorkspaceID
    workspace_name: str


class FormIntakeRead(Schema):
    id: uuid.UUID
    workspace_id: WorkspaceID
    workspace_name: str
    title: str
    description: str | None = None


class FormIntakeSubmissionRead(Schema):
    """A requester's own submission. Carries no run fields."""

    id: uuid.UUID
    reference: str
    form_id: uuid.UUID
    form_title: str
    workspace_id: WorkspaceID
    workspace_name: str
    submitted_at: datetime


# ----- Errors -----


class FormFieldError(Schema):
    field: str
    code: FormFieldErrorCode
    message: str


class FormErrorDetail(Schema):
    code: FormErrorCode
    message: str
    errors: list[FormFieldError] | None = Field(
        default=None, description="Per-field errors of a rejected submission."
    )
    issues: list[FormIssue] | None = Field(
        default=None, description="Issues of a rejected form config."
    )


class FormErrorResponse(Schema):
    detail: FormErrorDetail
