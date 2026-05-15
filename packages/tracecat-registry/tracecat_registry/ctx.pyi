from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, TypeVar
from uuid import UUID

from tracecat_registry import types
from tracecat_registry import types as registry_types
from tracecat_registry.sdk.agents import AgentConfig, RankableItem
from tracecat_registry.sdk.client import TracecatClient
from tracecat_registry.sdk.types import CasePriority, CaseSeverity, CaseStatus, Unset

T = TypeVar("T")

class _AgentsAsync:
    async def run(
        self,
        *,
        user_prompt: str,
        config: AgentConfig | None = ...,
        preset_slug: str | None = ...,
        preset_version: int | None = ...,
        max_requests: int = ...,
        max_tool_calls: int | None = ...,
    ) -> registry_types.AgentOutputRead: ...
    async def rank_items(
        self,
        *,
        items: list[RankableItem],
        criteria_prompt: str,
        model_name: str,
        model_provider: str,
        catalog_id: uuid.UUID | None = ...,
        model_settings: dict[str, object] | None = ...,
        max_requests: int = ...,
        retries: int = ...,
        base_url: str | None = ...,
        min_items: int | None = ...,
        max_items: int | None = ...,
    ) -> list[str | int]: ...
    async def rank_items_pairwise(
        self,
        *,
        items: list[RankableItem],
        criteria_prompt: str,
        model_name: str,
        model_provider: str,
        catalog_id: uuid.UUID | None = ...,
        id_field: str = ...,
        batch_size: int = ...,
        num_passes: int = ...,
        refinement_ratio: float = ...,
        model_settings: dict[str, object] | None = ...,
        max_requests: int = ...,
        retries: int = ...,
        base_url: str | None = ...,
        min_items: int | None = ...,
        max_items: int | None = ...,
    ) -> list[str | int]: ...
    async def list_presets(self) -> list[dict[str, Any]]: ...
    async def create_preset(
        self,
        *,
        name: str,
        model_name: str,
        model_provider: str,
        slug: str | Unset = ...,
        description: str | Unset = ...,
        instructions: str | Unset = ...,
        base_url: str | Unset = ...,
        output_type: str | dict[str, Any] | Unset = ...,
        actions: list[str] | Unset = ...,
    ) -> dict[str, Any]: ...
    async def get_preset(
        self,
        slug: str,
    ) -> dict[str, Any]: ...
    async def update_preset(
        self,
        slug: str,
        *,
        name: str | Unset = ...,
        new_slug: str | Unset = ...,
        description: str | Unset = ...,
        instructions: str | Unset = ...,
        model_name: str | Unset = ...,
        model_provider: str | Unset = ...,
        base_url: str | Unset = ...,
        output_type: str | dict[str, Any] | Unset = ...,
        actions: list[str] | Unset = ...,
    ) -> dict[str, Any]: ...
    async def delete_preset(
        self,
        slug: str,
    ) -> None: ...

class _Agents:
    @property
    def aio(self) -> _AgentsAsync: ...
    def run(
        self,
        *,
        user_prompt: str,
        config: AgentConfig | None = ...,
        preset_slug: str | None = ...,
        preset_version: int | None = ...,
        max_requests: int = ...,
        max_tool_calls: int | None = ...,
    ) -> registry_types.AgentOutputRead: ...
    def rank_items(
        self,
        *,
        items: list[RankableItem],
        criteria_prompt: str,
        model_name: str,
        model_provider: str,
        catalog_id: uuid.UUID | None = ...,
        model_settings: dict[str, object] | None = ...,
        max_requests: int = ...,
        retries: int = ...,
        base_url: str | None = ...,
        min_items: int | None = ...,
        max_items: int | None = ...,
    ) -> list[str | int]: ...
    def rank_items_pairwise(
        self,
        *,
        items: list[RankableItem],
        criteria_prompt: str,
        model_name: str,
        model_provider: str,
        catalog_id: uuid.UUID | None = ...,
        id_field: str = ...,
        batch_size: int = ...,
        num_passes: int = ...,
        refinement_ratio: float = ...,
        model_settings: dict[str, object] | None = ...,
        max_requests: int = ...,
        retries: int = ...,
        base_url: str | None = ...,
        min_items: int | None = ...,
        max_items: int | None = ...,
    ) -> list[str | int]: ...
    def list_presets(self) -> list[dict[str, Any]]: ...
    def create_preset(
        self,
        *,
        name: str,
        model_name: str,
        model_provider: str,
        slug: str | Unset = ...,
        description: str | Unset = ...,
        instructions: str | Unset = ...,
        base_url: str | Unset = ...,
        output_type: str | dict[str, Any] | Unset = ...,
        actions: list[str] | Unset = ...,
    ) -> dict[str, Any]: ...
    def get_preset(
        self,
        slug: str,
    ) -> dict[str, Any]: ...
    def update_preset(
        self,
        slug: str,
        *,
        name: str | Unset = ...,
        new_slug: str | Unset = ...,
        description: str | Unset = ...,
        instructions: str | Unset = ...,
        model_name: str | Unset = ...,
        model_provider: str | Unset = ...,
        base_url: str | Unset = ...,
        output_type: str | dict[str, Any] | Unset = ...,
        actions: list[str] | Unset = ...,
    ) -> dict[str, Any]: ...
    def delete_preset(
        self,
        slug: str,
    ) -> None: ...

class _CasesAsync:
    async def create_case(
        self,
        *,
        summary: str,
        description: str,
        status: CaseStatus = ...,
        priority: CasePriority = ...,
        severity: CaseSeverity = ...,
        assignee_id: str | None | Unset = ...,
        payload: dict[str, Any] | None | Unset = ...,
        tags: list[str] | None | Unset = ...,
        fields: dict[str, Any] | None | Unset = ...,
        dropdown_values: list[types.CaseDropdownValueInput] | None | Unset = ...,
    ) -> types.CaseRead: ...
    async def get_case(
        self,
        case_id: str,
        *,
        include_rows: bool | Unset = ...,
    ) -> types.CaseRead: ...
    async def update_case(
        self,
        case_id: str,
        *,
        summary: str | Unset = ...,
        description: str | None | Unset = ...,
        status: CaseStatus | Unset = ...,
        priority: CasePriority | Unset = ...,
        severity: CaseSeverity | Unset = ...,
        assignee_id: str | None | Unset = ...,
        payload: dict[str, Any] | None | Unset = ...,
        fields: dict[str, Any] | None | Unset = ...,
        tags: list[str] | None | Unset = ...,
        dropdown_values: list[types.CaseDropdownValueInput] | None | Unset = ...,
    ) -> types.CaseRead: ...
    async def delete_case(
        self,
        case_id: str,
    ) -> None: ...
    async def list_cases(
        self,
        *,
        limit: int = ...,
        cursor: str | Unset = ...,
        reverse: bool | Unset = ...,
        order_by: str | Unset = ...,
        sort: Literal["asc", "desc"] | Unset = ...,
        include_rows: bool | Unset = ...,
    ) -> types.CaseListResponse: ...
    async def search_cases(
        self,
        *,
        limit: int = ...,
        cursor: str | Unset = ...,
        reverse: bool | Unset = ...,
        search_term: str | Unset = ...,
        status: list[CaseStatus] | Unset = ...,
        priority: list[CasePriority] | Unset = ...,
        severity: list[CaseSeverity] | Unset = ...,
        assignee_id: list[str] | Unset = ...,
        tags: list[str] | Unset = ...,
        dropdown: list[str] | Unset = ...,
        order_by: str | Unset = ...,
        sort: Literal["asc", "desc"] | Unset = ...,
        start_time: datetime | str | Unset = ...,
        end_time: datetime | str | Unset = ...,
        updated_after: datetime | str | Unset = ...,
        updated_before: datetime | str | Unset = ...,
        include_rows: bool | Unset = ...,
    ) -> types.CaseListResponse: ...
    async def list_comments(
        self,
        case_id: str,
    ) -> list[types.CaseCommentRead]: ...
    async def list_comment_threads(
        self,
        case_id: str,
    ) -> list[types.CaseCommentThreadRead]: ...
    async def create_comment(
        self,
        case_id: str,
        *,
        content: str,
        parent_id: str | Unset = ...,
    ) -> types.CaseCommentRead: ...
    async def reply_to_comment(
        self,
        case_id: str,
        *,
        parent_comment_id: str,
        content: str,
    ) -> types.CaseComment: ...
    async def update_comment(
        self,
        case_id: str,
        comment_id: str,
        *,
        content: str,
    ) -> types.CaseCommentRead: ...
    async def update_comment_by_id(
        self,
        comment_id: str,
        *,
        content: str,
    ) -> types.CaseCommentRead: ...
    async def get_comment_thread(
        self,
        comment_id: str,
    ) -> types.CaseCommentThreadRead: ...
    async def delete_comment(
        self,
        case_id: str,
        comment_id: str,
    ) -> None: ...
    async def list_tags(
        self,
        case_id: str,
    ) -> list[types.TagRead]: ...
    async def add_tag(
        self,
        case_id: str,
        *,
        tag_id: str,
        create_if_missing: bool = ...,
    ) -> types.TagRead: ...
    async def remove_tag(
        self,
        case_id: str,
        *,
        tag_id: str,
    ) -> None: ...
    async def list_attachments(
        self,
        case_id: str,
    ) -> list[types.CaseAttachmentRead]: ...
    async def create_attachment(
        self,
        case_id: str,
        *,
        filename: str,
        content_base64: str,
        content_type: str = ...,
    ) -> types.CaseAttachmentRead: ...
    async def get_attachment(
        self,
        case_id: str,
        attachment_id: str,
        *,
        expiry: int | None = ...,
    ) -> types.CaseAttachmentDownloadResponse: ...
    async def get_attachment_download_url(
        self,
        case_id: str,
        attachment_id: str,
        *,
        expiry: int | None = ...,
    ) -> str: ...
    async def download_attachment(
        self,
        case_id: UUID,
        attachment_id: UUID,
    ) -> types.CaseAttachmentDownloadData: ...
    async def delete_attachment(
        self,
        case_id: UUID,
        attachment_id: UUID,
    ) -> None: ...
    async def list_events(
        self,
        case_id: str,
    ) -> types.CaseEventsWithUsers: ...
    async def assign_user(
        self,
        case_id: str,
        *,
        assignee_id: str,
    ) -> types.CaseRead: ...
    async def assign_user_by_email(
        self,
        case_id: str,
        *,
        email: str,
    ) -> types.Case: ...
    async def create_case_simple(
        self,
        *,
        summary: str,
        description: str,
        status: CaseStatus = ...,
        priority: CasePriority = ...,
        severity: CaseSeverity = ...,
        assignee_id: str | None | Unset = ...,
        payload: dict[str, Any] | None | Unset = ...,
        tags: list[str] | None | Unset = ...,
        fields: dict[str, Any] | None | Unset = ...,
        dropdown_values: list[types.CaseDropdownValueInput] | None | Unset = ...,
        create_missing_tags: bool = ...,
    ) -> types.Case: ...
    async def update_case_simple(
        self,
        case_id: str,
        *,
        summary: str | Unset = ...,
        description: str | None | Unset = ...,
        status: CaseStatus | Unset = ...,
        priority: CasePriority | Unset = ...,
        severity: CaseSeverity | Unset = ...,
        assignee_id: str | None | Unset = ...,
        payload: dict[str, Any] | None | Unset = ...,
        fields: dict[str, Any] | None | Unset = ...,
        tags: list[str] | None | Unset = ...,
        dropdown_values: list[types.CaseDropdownValueInput] | None | Unset = ...,
        append_description: bool = ...,
        create_missing_tags: bool = ...,
    ) -> types.Case: ...
    async def create_comment_simple(
        self,
        case_id: str,
        *,
        content: str,
        parent_id: str | Unset = ...,
        workflow_id: str | Unset = ...,
    ) -> types.CaseComment: ...
    async def update_comment_simple(
        self,
        comment_id: str,
        *,
        content: str,
    ) -> types.CaseComment: ...
    async def assign_user_simple(
        self,
        case_id: str,
        *,
        assignee_id: str,
    ) -> types.Case: ...
    async def get_attachment_metadata(
        self,
        case_id: UUID,
        attachment_id: UUID,
    ) -> types.CaseAttachmentRead: ...
    async def get_attachment_presigned_url(
        self,
        case_id: UUID,
        attachment_id: UUID,
        *,
        expiry: int | None = ...,
    ) -> str: ...
    async def get_case_metrics(
        self,
        case_ids: list[str],
    ) -> list[types.CaseDurationMetric]: ...
    async def create_task(
        self,
        case_id: str,
        *,
        title: str,
        description: str | None | Unset = ...,
        priority: str = ...,
        status: str = ...,
        assignee_id: str | None | Unset = ...,
        workflow_id: str | None | Unset = ...,
        default_trigger_values: dict[str, Any] | None | Unset = ...,
    ) -> types.CaseTaskRead: ...
    async def get_task(
        self,
        task_id: str,
    ) -> types.CaseTaskRead: ...
    async def list_tasks(
        self,
        case_id: str,
    ) -> list[types.CaseTaskRead]: ...
    async def update_task(
        self,
        task_id: str,
        *,
        title: str | Unset = ...,
        description: str | None | Unset = ...,
        priority: str | Unset = ...,
        status: str | Unset = ...,
        assignee_id: str | None | Unset = ...,
        workflow_id: str | None | Unset = ...,
        default_trigger_values: dict[str, Any] | None | Unset = ...,
    ) -> types.CaseTaskRead: ...
    async def delete_task(
        self,
        task_id: str,
    ) -> None: ...
    async def list_case_rows(
        self,
        case_id: str,
        *,
        limit: int = ...,
        cursor: str | Unset = ...,
        reverse: bool | Unset = ...,
    ) -> dict[str, Any]: ...
    async def link_case_row(
        self,
        case_id: str,
        *,
        table_id: str,
        row_id: str,
    ) -> types.CaseTableRowRead: ...
    async def unlink_case_row(
        self,
        case_id: str,
        *,
        table_id: str,
        row_id: str,
    ) -> None: ...
    async def insert_case_row(
        self,
        case_id: str,
        *,
        table_id: str,
        row: dict[str, Any],
    ) -> types.CaseTableRowRead: ...

class _Cases:
    @property
    def aio(self) -> _CasesAsync: ...
    def create_case(
        self,
        *,
        summary: str,
        description: str,
        status: CaseStatus = ...,
        priority: CasePriority = ...,
        severity: CaseSeverity = ...,
        assignee_id: str | None | Unset = ...,
        payload: dict[str, Any] | None | Unset = ...,
        tags: list[str] | None | Unset = ...,
        fields: dict[str, Any] | None | Unset = ...,
        dropdown_values: list[types.CaseDropdownValueInput] | None | Unset = ...,
    ) -> types.CaseRead: ...
    def get_case(
        self,
        case_id: str,
        *,
        include_rows: bool | Unset = ...,
    ) -> types.CaseRead: ...
    def update_case(
        self,
        case_id: str,
        *,
        summary: str | Unset = ...,
        description: str | None | Unset = ...,
        status: CaseStatus | Unset = ...,
        priority: CasePriority | Unset = ...,
        severity: CaseSeverity | Unset = ...,
        assignee_id: str | None | Unset = ...,
        payload: dict[str, Any] | None | Unset = ...,
        fields: dict[str, Any] | None | Unset = ...,
        tags: list[str] | None | Unset = ...,
        dropdown_values: list[types.CaseDropdownValueInput] | None | Unset = ...,
    ) -> types.CaseRead: ...
    def delete_case(
        self,
        case_id: str,
    ) -> None: ...
    def list_cases(
        self,
        *,
        limit: int = ...,
        cursor: str | Unset = ...,
        reverse: bool | Unset = ...,
        order_by: str | Unset = ...,
        sort: Literal["asc", "desc"] | Unset = ...,
        include_rows: bool | Unset = ...,
    ) -> types.CaseListResponse: ...
    def search_cases(
        self,
        *,
        limit: int = ...,
        cursor: str | Unset = ...,
        reverse: bool | Unset = ...,
        search_term: str | Unset = ...,
        status: list[CaseStatus] | Unset = ...,
        priority: list[CasePriority] | Unset = ...,
        severity: list[CaseSeverity] | Unset = ...,
        assignee_id: list[str] | Unset = ...,
        tags: list[str] | Unset = ...,
        dropdown: list[str] | Unset = ...,
        order_by: str | Unset = ...,
        sort: Literal["asc", "desc"] | Unset = ...,
        start_time: datetime | str | Unset = ...,
        end_time: datetime | str | Unset = ...,
        updated_after: datetime | str | Unset = ...,
        updated_before: datetime | str | Unset = ...,
        include_rows: bool | Unset = ...,
    ) -> types.CaseListResponse: ...
    def list_comments(
        self,
        case_id: str,
    ) -> list[types.CaseCommentRead]: ...
    def list_comment_threads(
        self,
        case_id: str,
    ) -> list[types.CaseCommentThreadRead]: ...
    def create_comment(
        self,
        case_id: str,
        *,
        content: str,
        parent_id: str | Unset = ...,
    ) -> types.CaseCommentRead: ...
    def reply_to_comment(
        self,
        case_id: str,
        *,
        parent_comment_id: str,
        content: str,
    ) -> types.CaseComment: ...
    def update_comment(
        self,
        case_id: str,
        comment_id: str,
        *,
        content: str,
    ) -> types.CaseCommentRead: ...
    def update_comment_by_id(
        self,
        comment_id: str,
        *,
        content: str,
    ) -> types.CaseCommentRead: ...
    def get_comment_thread(
        self,
        comment_id: str,
    ) -> types.CaseCommentThreadRead: ...
    def delete_comment(
        self,
        case_id: str,
        comment_id: str,
    ) -> None: ...
    def list_tags(
        self,
        case_id: str,
    ) -> list[types.TagRead]: ...
    def add_tag(
        self,
        case_id: str,
        *,
        tag_id: str,
        create_if_missing: bool = ...,
    ) -> types.TagRead: ...
    def remove_tag(
        self,
        case_id: str,
        *,
        tag_id: str,
    ) -> None: ...
    def list_attachments(
        self,
        case_id: str,
    ) -> list[types.CaseAttachmentRead]: ...
    def create_attachment(
        self,
        case_id: str,
        *,
        filename: str,
        content_base64: str,
        content_type: str = ...,
    ) -> types.CaseAttachmentRead: ...
    def get_attachment(
        self,
        case_id: str,
        attachment_id: str,
        *,
        expiry: int | None = ...,
    ) -> types.CaseAttachmentDownloadResponse: ...
    def get_attachment_download_url(
        self,
        case_id: str,
        attachment_id: str,
        *,
        expiry: int | None = ...,
    ) -> str: ...
    def download_attachment(
        self,
        case_id: UUID,
        attachment_id: UUID,
    ) -> types.CaseAttachmentDownloadData: ...
    def delete_attachment(
        self,
        case_id: UUID,
        attachment_id: UUID,
    ) -> None: ...
    def list_events(
        self,
        case_id: str,
    ) -> types.CaseEventsWithUsers: ...
    def assign_user(
        self,
        case_id: str,
        *,
        assignee_id: str,
    ) -> types.CaseRead: ...
    def assign_user_by_email(
        self,
        case_id: str,
        *,
        email: str,
    ) -> types.Case: ...
    def create_case_simple(
        self,
        *,
        summary: str,
        description: str,
        status: CaseStatus = ...,
        priority: CasePriority = ...,
        severity: CaseSeverity = ...,
        assignee_id: str | None | Unset = ...,
        payload: dict[str, Any] | None | Unset = ...,
        tags: list[str] | None | Unset = ...,
        fields: dict[str, Any] | None | Unset = ...,
        dropdown_values: list[types.CaseDropdownValueInput] | None | Unset = ...,
        create_missing_tags: bool = ...,
    ) -> types.Case: ...
    def update_case_simple(
        self,
        case_id: str,
        *,
        summary: str | Unset = ...,
        description: str | None | Unset = ...,
        status: CaseStatus | Unset = ...,
        priority: CasePriority | Unset = ...,
        severity: CaseSeverity | Unset = ...,
        assignee_id: str | None | Unset = ...,
        payload: dict[str, Any] | None | Unset = ...,
        fields: dict[str, Any] | None | Unset = ...,
        tags: list[str] | None | Unset = ...,
        dropdown_values: list[types.CaseDropdownValueInput] | None | Unset = ...,
        append_description: bool = ...,
        create_missing_tags: bool = ...,
    ) -> types.Case: ...
    def create_comment_simple(
        self,
        case_id: str,
        *,
        content: str,
        parent_id: str | Unset = ...,
        workflow_id: str | Unset = ...,
    ) -> types.CaseComment: ...
    def update_comment_simple(
        self,
        comment_id: str,
        *,
        content: str,
    ) -> types.CaseComment: ...
    def assign_user_simple(
        self,
        case_id: str,
        *,
        assignee_id: str,
    ) -> types.Case: ...
    def get_attachment_metadata(
        self,
        case_id: UUID,
        attachment_id: UUID,
    ) -> types.CaseAttachmentRead: ...
    def get_attachment_presigned_url(
        self,
        case_id: UUID,
        attachment_id: UUID,
        *,
        expiry: int | None = ...,
    ) -> str: ...
    def get_case_metrics(
        self,
        case_ids: list[str],
    ) -> list[types.CaseDurationMetric]: ...
    def create_task(
        self,
        case_id: str,
        *,
        title: str,
        description: str | None | Unset = ...,
        priority: str = ...,
        status: str = ...,
        assignee_id: str | None | Unset = ...,
        workflow_id: str | None | Unset = ...,
        default_trigger_values: dict[str, Any] | None | Unset = ...,
    ) -> types.CaseTaskRead: ...
    def get_task(
        self,
        task_id: str,
    ) -> types.CaseTaskRead: ...
    def list_tasks(
        self,
        case_id: str,
    ) -> list[types.CaseTaskRead]: ...
    def update_task(
        self,
        task_id: str,
        *,
        title: str | Unset = ...,
        description: str | None | Unset = ...,
        priority: str | Unset = ...,
        status: str | Unset = ...,
        assignee_id: str | None | Unset = ...,
        workflow_id: str | None | Unset = ...,
        default_trigger_values: dict[str, Any] | None | Unset = ...,
    ) -> types.CaseTaskRead: ...
    def delete_task(
        self,
        task_id: str,
    ) -> None: ...
    def list_case_rows(
        self,
        case_id: str,
        *,
        limit: int = ...,
        cursor: str | Unset = ...,
        reverse: bool | Unset = ...,
    ) -> dict[str, Any]: ...
    def link_case_row(
        self,
        case_id: str,
        *,
        table_id: str,
        row_id: str,
    ) -> types.CaseTableRowRead: ...
    def unlink_case_row(
        self,
        case_id: str,
        *,
        table_id: str,
        row_id: str,
    ) -> None: ...
    def insert_case_row(
        self,
        case_id: str,
        *,
        table_id: str,
        row: dict[str, Any],
    ) -> types.CaseTableRowRead: ...

class _DeduplicateAsync:
    async def create_digests(
        self,
        digests: list[str],
        expire_seconds: int,
    ) -> list[bool]: ...

class _Deduplicate:
    @property
    def aio(self) -> _DeduplicateAsync: ...
    def create_digests(
        self,
        digests: list[str],
        expire_seconds: int,
    ) -> list[bool]: ...

class _TablesAsync:
    async def list_tables(self) -> list[types.Table]: ...
    async def create_table(
        self,
        *,
        name: str,
        columns: list[dict[str, Any]] | Unset = ...,
        raise_on_duplicate: bool = ...,
    ) -> types.Table: ...
    async def get_table_metadata(
        self,
        name: str,
    ) -> types.TableRead: ...
    async def lookup(
        self,
        *,
        table: str,
        column: str,
        value: Any,
    ) -> dict[str, Any] | None: ...
    async def lookup_many(
        self,
        *,
        table: str,
        column: str,
        value: Any,
        limit: int | Unset = ...,
    ) -> list[dict[str, Any]]: ...
    async def exists(
        self,
        *,
        table: str,
        column: str,
        value: Any,
    ) -> bool: ...
    async def search_rows(
        self,
        *,
        table: str,
        search_term: str | Unset = ...,
        start_time: datetime | str | Unset = ...,
        end_time: datetime | str | Unset = ...,
        updated_before: datetime | str | Unset = ...,
        updated_after: datetime | str | Unset = ...,
        cursor: str | Unset = ...,
        reverse: bool | Unset = ...,
        limit: int | Unset = ...,
    ) -> types.TableSearchResponse | list[dict[str, Any]]: ...
    async def insert_row(
        self,
        *,
        table: str,
        row_data: dict[str, Any],
        upsert: bool = ...,
    ) -> dict[str, Any]: ...
    async def insert_rows(
        self,
        *,
        table: str,
        rows_data: list[dict[str, Any]],
        upsert: bool = ...,
    ) -> int: ...
    async def update_row(
        self,
        *,
        table: str,
        row_id: str,
        row_data: dict[str, Any],
    ) -> dict[str, Any]: ...
    async def delete_row(
        self,
        *,
        table: str,
        row_id: str,
    ) -> None: ...
    async def download(
        self,
        *,
        table: str,
        format: Literal["json", "ndjson", "csv", "markdown"] | Unset = ...,
        limit: int | Unset = ...,
    ) -> list[dict[str, Any]] | str: ...

class _Tables:
    @property
    def aio(self) -> _TablesAsync: ...
    def list_tables(self) -> list[types.Table]: ...
    def create_table(
        self,
        *,
        name: str,
        columns: list[dict[str, Any]] | Unset = ...,
        raise_on_duplicate: bool = ...,
    ) -> types.Table: ...
    def get_table_metadata(
        self,
        name: str,
    ) -> types.TableRead: ...
    def lookup(
        self,
        *,
        table: str,
        column: str,
        value: Any,
    ) -> dict[str, Any] | None: ...
    def lookup_many(
        self,
        *,
        table: str,
        column: str,
        value: Any,
        limit: int | Unset = ...,
    ) -> list[dict[str, Any]]: ...
    def exists(
        self,
        *,
        table: str,
        column: str,
        value: Any,
    ) -> bool: ...
    def search_rows(
        self,
        *,
        table: str,
        search_term: str | Unset = ...,
        start_time: datetime | str | Unset = ...,
        end_time: datetime | str | Unset = ...,
        updated_before: datetime | str | Unset = ...,
        updated_after: datetime | str | Unset = ...,
        cursor: str | Unset = ...,
        reverse: bool | Unset = ...,
        limit: int | Unset = ...,
    ) -> types.TableSearchResponse | list[dict[str, Any]]: ...
    def insert_row(
        self,
        *,
        table: str,
        row_data: dict[str, Any],
        upsert: bool = ...,
    ) -> dict[str, Any]: ...
    def insert_rows(
        self,
        *,
        table: str,
        rows_data: list[dict[str, Any]],
        upsert: bool = ...,
    ) -> int: ...
    def update_row(
        self,
        *,
        table: str,
        row_id: str,
        row_data: dict[str, Any],
    ) -> dict[str, Any]: ...
    def delete_row(
        self,
        *,
        table: str,
        row_id: str,
    ) -> None: ...
    def download(
        self,
        *,
        table: str,
        format: Literal["json", "ndjson", "csv", "markdown"] | Unset = ...,
        limit: int | Unset = ...,
    ) -> list[dict[str, Any]] | str: ...

class _VariablesAsync:
    async def get(
        self,
        name: str,
        key: str,
        *,
        environment: str | Unset = ...,
    ) -> Any: ...
    async def get_or_default(
        self,
        name: str,
        key: str,
        default: T,
        *,
        environment: str | Unset = ...,
    ) -> Any | T: ...
    async def get_variable(
        self,
        name: str,
        *,
        environment: str | Unset = ...,
    ) -> dict[str, Any]: ...

class _Variables:
    @property
    def aio(self) -> _VariablesAsync: ...
    def get(
        self,
        name: str,
        key: str,
        *,
        environment: str | Unset = ...,
    ) -> Any: ...
    def get_or_default(
        self,
        name: str,
        key: str,
        default: T,
        *,
        environment: str | Unset = ...,
    ) -> Any | T: ...
    def get_variable(
        self,
        name: str,
        *,
        environment: str | Unset = ...,
    ) -> dict[str, Any]: ...

class _WorkflowsAsync:
    async def execute(
        self,
        *,
        workflow_id: str | None = ...,
        workflow_alias: str | None = ...,
        trigger_inputs: Any | Unset = ...,
        environment: str | None = ...,
        wait_strategy: Literal["wait", "detach"] = ...,
        timeout: float | None = ...,
        poll_interval: float = ...,
        parent_workflow_execution_id: str | None = ...,
    ) -> dict[str, Any]: ...
    async def get_status(
        self,
        workflow_execution_id: str,
    ) -> dict[str, Any]: ...

class _Workflows:
    @property
    def aio(self) -> _WorkflowsAsync: ...
    def execute(
        self,
        *,
        workflow_id: str | None = ...,
        workflow_alias: str | None = ...,
        trigger_inputs: Any | Unset = ...,
        environment: str | None = ...,
        wait_strategy: Literal["wait", "detach"] = ...,
        timeout: float | None = ...,
        poll_interval: float = ...,
        parent_workflow_execution_id: str | None = ...,
    ) -> dict[str, Any]: ...
    def get_status(
        self,
        workflow_execution_id: str,
    ) -> dict[str, Any]: ...

agents: _Agents
cases: _Cases
deduplicate: _Deduplicate
tables: _Tables
variables: _Variables
workflows: _Workflows

workspace_id: str
workflow_id: str
run_id: str
wf_exec_id: str | None
environment: str
api_url: str
executor_url: str
token: str
client: TracecatClient
