import textwrap

import yaml
from pydantic import BaseModel

from tracecat.db.schemas import Case


class CaseCopilotPrompts(BaseModel):
    """Prompts for building a runbook to case."""

    case: Case

    @property
    def instructions(self) -> str:
        """Build the instructions for the case copilot."""
        updated_at = self.case.updated_at.isoformat()
        case_data = yaml.dump(self.case.model_dump(mode="json"), indent=2)
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
