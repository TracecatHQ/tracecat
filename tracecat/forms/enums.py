"""Enums for forms."""

from enum import StrEnum


class FormStatus(StrEnum):
    """Whether a form accepts submissions."""

    ONLINE = "online"
    OFFLINE = "offline"


class FormWidget(StrEnum):
    """Input controls a form field can render as."""

    TEXT = "text"
    LONG_TEXT = "long_text"
    NUMBER = "number"
    TOGGLE = "toggle"
    SELECT = "select"
    MULTI_SELECT = "multi_select"
    CARDS = "cards"
    TAGS = "tags"
    DATE = "date"
    DATETIME = "datetime"
    USER = "user"
    FILE = "file"


class FormInputKind(StrEnum):
    """Workflow input types as the widget registry sees them."""

    STR = "str"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    DATETIME = "datetime"
    ENUM = "enum"
    LIST_STR = "list[str]"
    LIST_ENUM = "list[enum]"
    DICT = "dict"
    LIST_DICT = "list[dict]"
    ANY = "any"
    UNSUPPORTED = "unsupported"
    """Any other type: duration, None, other lists and unions."""


class FormNoticeTone(StrEnum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    DANGER = "danger"


class FormAutofillSource(StrEnum):
    """Values the server fills in on submit."""

    REQUESTER = "requester"
    REQUESTER_USER_ID = "requester.user_id"
    REQUESTER_EMAIL = "requester.email"
    REQUESTER_FIRST_NAME = "requester.first_name"
    REQUESTER_LAST_NAME = "requester.last_name"
    SUBMISSION_ID = "submission.id"


class FormIssueSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class FormIssueCode(StrEnum):
    """Problems the resolver finds in a form config."""

    UNKNOWN_FIELD = "unknown_field"
    DUPLICATE_FIELD = "duplicate_field"
    INCOMPATIBLE_WIDGET = "incompatible_widget"
    NO_WIDGET_FOR_TYPE = "no_widget_for_type"
    UNSUPPORTED_WIDGET_OPTION = "unsupported_widget_option"
    OPTIONS_REQUIRED = "options_required"
    OPTIONS_NOT_SUPPORTED = "options_not_supported"
    OPTION_NOT_IN_ENUM = "option_not_in_enum"
    DEFAULT_INVALID = "default_invalid"
    CONDITIONAL_FIELD_REQUIRED = "conditional_field_required"
    VISIBLE_IF_UNKNOWN_FIELD = "visible_if_unknown_field"
    VISIBLE_IF_INVALID_CONTROLLER = "visible_if_invalid_controller"
    VISIBLE_IF_INVALID_VALUE = "visible_if_invalid_value"
    AUTOFILL_TYPE_MISMATCH = "autofill_type_mismatch"
    HIDDEN_WITH_AUTOFILL = "hidden_with_autofill"
    UNRENDERED_OPTIONAL_INPUT = "unrendered_optional_input"
    INTEGER_OUT_OF_RANGE = "integer_out_of_range"


class FormErrorCode(StrEnum):
    """Machine-readable failures of a forms request."""

    NOT_FOUND = "not_found"
    ALREADY_EXISTS = "already_exists"
    FORM_CONFIG_INVALID = "form_config_invalid"
    FORM_WORKFLOW_UNPUBLISHED = "form_workflow_unpublished"
    FORM_OFFLINE = "form_offline"
    FORM_UNAVAILABLE = "form_unavailable"
    FORM_CHANGED = "form_changed"
    FORM_VALIDATION_ERROR = "form_validation_error"
    WORKFLOW_START_FAILED = "workflow_start_failed"
    WORKFLOW_START_UNCONFIRMED = "workflow_start_unconfirmed"
    FILE_FIELD_INVALID = "file_field_invalid"
    FILE_INVALID = "file_invalid"
    FILE_TYPE_NOT_ALLOWED = "file_type_not_allowed"
    FILE_TOO_LARGE = "file_too_large"
    FILE_LIMIT_REACHED = "file_limit_reached"
    FILE_UPLOAD_FAILED = "file_upload_failed"


class FormFieldErrorCode(StrEnum):
    """Why a submitted value was rejected."""

    REQUIRED = "required"
    UNKNOWN_FIELD = "unknown_field"
    INVALID_VALUE = "invalid_value"
    INVALID_OPTION = "invalid_option"
    INVALID_DATE = "invalid_date"
    INVALID_USER = "invalid_user"
    INVALID_FILE = "invalid_file"
    TOO_MANY_FILES = "too_many_files"
