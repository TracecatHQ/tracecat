from tracecat.cases.attachments.service import (
    _attachment_allowlist_from_workspace_setting,
)


def test_attachment_allowlist_setting_none_inherits_defaults() -> None:
    assert _attachment_allowlist_from_workspace_setting(None) is None


def test_attachment_allowlist_setting_empty_list_is_preserved() -> None:
    assert _attachment_allowlist_from_workspace_setting([]) == []


def test_attachment_allowlist_setting_values_are_copied_to_list() -> None:
    source = (".pdf", ".txt")

    assert _attachment_allowlist_from_workspace_setting(source) == [".pdf", ".txt"]
