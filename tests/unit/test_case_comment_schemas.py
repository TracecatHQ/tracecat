import pytest
from pydantic import ValidationError

from tracecat.cases.schemas import CaseCommentCreate

WORKFLOW_ID = "wf-00000000000000000000000000000001"


def test_create_accepts_empty_content_with_workflow() -> None:
    params = CaseCommentCreate(content="", workflow_id=WORKFLOW_ID)

    assert params.content == ""
    assert params.workflow_id is not None


def test_create_strips_whitespace_only_content_with_workflow() -> None:
    params = CaseCommentCreate(content="   \n\t ", workflow_id=WORKFLOW_ID)

    assert params.content == ""


@pytest.mark.parametrize("content", ["", "   ", "\n\t"])
def test_create_rejects_blank_content_without_workflow(content: str) -> None:
    with pytest.raises(ValidationError, match="Comment content cannot be blank"):
        CaseCommentCreate(content=content)


def test_create_strips_surrounding_whitespace() -> None:
    params = CaseCommentCreate(content="  hello  ")

    assert params.content == "hello"
