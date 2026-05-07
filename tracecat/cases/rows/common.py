from starlette.status import HTTP_409_CONFLICT

from tracecat.api.common import KnownDatabaseError

CASE_ROW_LINK_CONSTRAINT_ERRORS = {
    "uq_case_table_row_link": KnownDatabaseError(
        status_code=HTTP_409_CONFLICT,
        code="CASE_ROW_ALREADY_LINKED",
        message="This table row is already linked to the case.",
    )
}
