"""Form UDFs.

These actions are always registered. The internal routes they call are gated
on the ``forms`` feature flag and entitlement on the server side.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from typing_extensions import Doc

from tracecat_registry import ctx, registry

_CONFIG_DOC = (
    "The whole form config; it replaces the stored one. Keys, all optional: "
    "`title`, `description` (markdown), `success_message`, and `layout`, an "
    "ordered list of entries. An entry is one of `{field: <input name>}`, "
    "`{heading: <text>}`, `{text: <markdown>}`, `{notice: <text>, tone: "
    "info|success|warning|danger}` or `{divider: true}`. A field entry may add "
    "`label`, `help`, `placeholder`, `widget` (text, long_text, number, toggle, "
    "select, multi_select, cards, tags, date, datetime, user, file), `options` "
    "(`[{value, label, description}]`), `visible_if` (`{field, equals}`), "
    "`autofill` (requester, requester.user_id, requester.email, "
    "requester.first_name, requester.last_name, submission.id) or `hidden`. "
    "A `file` field may add `accept` (extensions or MIME types, e.g. "
    '`[".pdf", "image/*"]`) and `max_files` (1 to 10; more than 1 needs a '
    "`list[dict]` input). "
    "Workflow inputs left out of `layout` are appended in schema order. Omit "
    "`config` to leave it unchanged."
)


@registry.register(
    default_title="List forms",
    display_group="Forms",
    description="List the workspace's forms, newest first.",
    namespace="core.forms",
    required_entitlements=["forms"],
)
async def list_forms(
    limit: Annotated[int, Doc("Maximum number of forms to return.")] = 20,
    cursor: Annotated[str | None, Doc("`next_cursor` from a previous page.")] = None,
) -> dict[str, Any]:
    return await ctx.forms.aio.list_forms(limit=limit, cursor=cursor)


@registry.register(
    default_title="Get form",
    display_group="Forms",
    description=(
        "Get a form: its status, its config, the issues the config has against "
        "the workflow's inputs, and its URL."
    ),
    namespace="core.forms",
    required_entitlements=["forms"],
)
async def get_form(
    form_id: Annotated[str, Doc("The form ID, as returned by `list_forms`.")],
) -> dict[str, Any]:
    return await ctx.forms.aio.get_form(form_id)


@registry.register(
    default_title="Update form",
    display_group="Forms",
    description=(
        "Update the form of a workflow, creating it if the workflow has none. "
        "A form turns the workflow's input schema into a page people fill in; "
        "submitting it runs the workflow. Returns the form, including any "
        "`issues` with the config."
    ),
    namespace="core.forms",
    required_entitlements=["forms"],
)
async def update_form(
    workflow_id: Annotated[
        str,
        Doc("The workflow the form belongs to (short `wf_...` or full ID)."),
    ],
    status: Annotated[
        Literal["online", "offline"] | None,
        Doc(
            "`online` lets people submit the form (the workflow must be "
            "published); `offline` stops submissions. Omit to leave unchanged."
        ),
    ] = None,
    config: Annotated[dict[str, Any] | None, Doc(_CONFIG_DOC)] = None,
) -> dict[str, Any]:
    return await ctx.forms.aio.update_form(
        workflow_id=workflow_id, status=status, config=config
    )


@registry.register(
    default_title="List form submissions",
    display_group="Forms",
    description="List a form's submissions with their answers, newest first.",
    namespace="core.forms",
    required_entitlements=["forms"],
)
async def list_submissions(
    form_id: Annotated[str, Doc("The form ID.")],
    limit: Annotated[int, Doc("Maximum number of submissions to return.")] = 20,
    cursor: Annotated[str | None, Doc("`next_cursor` from a previous page.")] = None,
    submitted_after: Annotated[
        datetime | None, Doc("Only submissions at or after this time.")
    ] = None,
    submitted_before: Annotated[
        datetime | None, Doc("Only submissions before this time.")
    ] = None,
) -> dict[str, Any]:
    return await ctx.forms.aio.list_submissions(
        form_id,
        limit=limit,
        cursor=cursor,
        submitted_after=submitted_after,
        submitted_before=submitted_before,
    )


@registry.register(
    default_title="Get form submission",
    display_group="Forms",
    description="Get one form submission with its answers and who submitted it.",
    namespace="core.forms",
    required_entitlements=["forms"],
)
async def get_submission(
    submission_id: Annotated[
        str,
        Doc(
            "The submission ID. A form-triggered workflow gets its own from an "
            "input with `autofill: submission.id`."
        ),
    ],
) -> dict[str, Any]:
    return await ctx.forms.aio.get_submission(submission_id)


@registry.register(
    default_title="Delete form submission",
    display_group="Forms",
    description=(
        "Delete a form submission and its stored answers. The workflow run it "
        "started keeps its own copy of the inputs. Files uploaded with the "
        "submission are deleted too."
    ),
    namespace="core.forms",
    required_entitlements=["forms"],
)
async def delete_submission(
    submission_id: Annotated[str, Doc("The submission ID.")],
) -> None:
    await ctx.forms.aio.delete_submission(submission_id)


@registry.register(
    default_title="Download form file",
    display_group="Forms",
    description=(
        "Download a file uploaded with a form submission. File integrity is "
        "verified via SHA256."
    ),
    namespace="core.forms",
    required_entitlements=["forms"],
)
async def download_file(
    file_id: Annotated[
        str,
        Doc(
            "The file ID: the `id` of the file object a `file` field puts in "
            "the workflow's inputs."
        ),
    ],
) -> dict[str, Any]:
    return await ctx.forms.aio.download_file(file_id)
