from tracecat.cases.agent_invocations.output import render_agent_output_as_comment
from tracecat.cases.schemas import CASE_COMMENT_MAX_LENGTH

_TRUNCATION_NOTICE = "\n\n[Agent output truncated]"


def test_preserves_output_at_comment_limit() -> None:
    content = "a" * CASE_COMMENT_MAX_LENGTH

    assert render_agent_output_as_comment(content) == content


def test_truncates_text_output_with_visible_notice() -> None:
    rendered = render_agent_output_as_comment("a" * (CASE_COMMENT_MAX_LENGTH + 1))

    assert len(rendered) == CASE_COMMENT_MAX_LENGTH
    assert rendered.endswith(_TRUNCATION_NOTICE)


def test_truncates_rendered_structured_output() -> None:
    rendered = render_agent_output_as_comment(
        {"content": "a" * CASE_COMMENT_MAX_LENGTH}
    )

    assert len(rendered) == CASE_COMMENT_MAX_LENGTH
    assert rendered.endswith(_TRUNCATION_NOTICE)
