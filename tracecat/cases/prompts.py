import json
import textwrap

import yaml
from pydantic import BaseModel

from tracecat.db.models import Case

MAX_CASE_PROMPT_DATA_CHARS = 200_000
"""Upper bound on case data embedded in the case copilot instructions."""


class CaseCopilotPrompts(BaseModel):
    """Prompts for the case copilot chat assistant."""

    case: Case

    @property
    def instructions(self) -> str:
        """Build the instructions for the case copilot."""
        updated_at = self.case.updated_at.isoformat()
        case_data = self._case_data()
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

    def _case_data(self) -> str:
        """Serialize the case, truncating the payload to fit the prompt budget."""
        case_dict = self.case.to_dict()
        case_data = yaml.dump(case_dict, indent=2)
        if len(case_data) <= MAX_CASE_PROMPT_DATA_CHARS:
            return case_data

        payload = case_dict.pop("payload", None)
        case_data = yaml.dump(case_dict, indent=2)
        payload_text = json.dumps(payload, default=str)
        budget = max(MAX_CASE_PROMPT_DATA_CHARS - len(case_data), 0)
        if len(payload_text) <= budget:
            return f"{case_data}<Payload>\n{payload_text}\n</Payload>\n"
        return (
            f"{case_data}"
            f'<PayloadPreview truncated="true" total_chars="{len(payload_text)}"'
            f' shown_chars="{budget}">\n'
            f"{payload_text[:budget]}\n"
            "</PayloadPreview>\n"
            "The case payload is too large to include in full. Call"
            " core.cases.get_case with the case UUID to read the full payload"
            " when the preview is not enough.\n"
        )

    @property
    def user_prompt(self) -> str:
        raise NotImplementedError(
            "User prompt is not implemented for the case copilot."
        )
