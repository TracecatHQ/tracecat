from __future__ import annotations

import textwrap
from datetime import datetime
from typing import Any
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, computed_field

from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.db.models import Case


class CasePromptData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    case_number: int
    summary: str
    description: str
    status: CaseStatus
    priority: CasePriority
    severity: CaseSeverity
    payload: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
    assignee_id: UUID | None = None

    @computed_field
    def short_id(self) -> str | None:
        return f"CASE-{self.case_number:04d}"


class CaseCopilotPrompts(BaseModel):
    """Prompts for the case copilot chat assistant."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    case: Case

    @property
    def instructions(self) -> str:
        """Build the instructions for the case copilot."""
        updated_at = self.case.updated_at.isoformat()
        case_data = yaml.dump(self._serialize_case(), indent=2)
        return textwrap.dedent(f"""
            You are a helpful case management assistant that helps analysts in security and IT operations resolve cases / tickets efficiently and accurately. You will be given a case with a summary, description, and payload inside the <Case> tag.

            IMPORTANT: Do not execute any actions or tools that are not explicitly requested by the user. You are an assistant, not a replacement for the analyst.
            IMPORTANT: If you have suggestions or recommendations based on the case, you must ask the user for explicit permission before proceeding.
            IMPORTANT: Assist with defensive security tasks only. Refuse to create, modify, or improve code that may be used maliciously. Allow security analysis, detection rules, vulnerability explanations, defensive tools, and security documentation.
            IMPORTANT: You must NEVER generate or guess URLs for the user unless you are confident that the URLs are for helping the user with programming. You may use URLs provided by the user in their messages or local files.
            IMPORTANT: When using tools or APIs that require the case ID, ALWAYS supply the canonical UUID found in the <Case> "id" field. The "short_id" field is for human-readable display only and must NOT be used as an identifier in tool calls.

            <CaseId description="This is the ID to use in case management CRUD APIs">{self.case.id}</CaseId>
            <Case description="This is the case you are working on with the summary, description, and payload. It was last updated at {updated_at}.">
            {case_data}
            </Case>
        """)

    @property
    def user_prompt(self) -> str:
        raise NotImplementedError(
            "User prompt is not implemented for the case copilot."
        )

    def _serialize_case(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of the case."""
        prompt_case = CasePromptData.model_validate(self.case, from_attributes=True)
        case_data = prompt_case.model_dump(mode="json", exclude_none=True)
        tags = getattr(self.case, "__dict__", {}).get("tags")
        if tags:
            case_data["tags"] = [
                {
                    "id": str(tag.id),
                    "name": tag.name,
                    "ref": tag.ref,
                    "color": tag.color,
                }
                for tag in tags
            ]
        return case_data
