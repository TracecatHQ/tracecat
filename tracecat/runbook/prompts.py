"""Service layer for runbook-related prompts."""

import json
import textwrap

import yaml
from pydantic import BaseModel
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from tracecat.chat.models import ChatMessage
from tracecat.db.schemas import Case, Runbook

NEW_RUNBOOK_INSTRUCTIONS = textwrap.dedent(
    """\
    # Objective
    Describe the goal of this runbook.

    ## Steps
    1. Describe the first action the agent should perform.
    2. Add additional steps as needed.

    ## Tools
    - List the tools required and how to use them.
    """
)


RUNBOOK_REQUIREMENTS = textwrap.dedent("""
    <Requirements>
    - Output ONLY the runbook as Markdown; no prose before/after
    - Do NOT wrap the entire runbook in a single code block
    - Use Markdown: headers (#, ##), lists (-), bold (**)
    - Use fenced blocks only for actual code/commands
    - Include sections: Objective, Tools, Steps
    - Objective must be generalized (no specific IDs/private values)
    - Tools are provided as <Tools>
    - Trigger section (Markdown):
        - Start with a single line: **Execute when**:
        - Then list 1 to 3 concise, human-readable conditions as bullets, each referencing Alert fields via JSONPath or placeholders
        - Add a **Do not execute when** subsection (optional, 1-3 bullet points) listing clear exclusions
        - Keep the language human-readable; avoid raw code where possible. Use JSONPath only to point to specific fields
    </Requirements>
""")


def _reduce_messages_to_steps(messages: list[ChatMessage]) -> str:
    """Reduce chat messages to a single steps string.

    The runbook string should be an executable instruction set for an agent.

    Phase 1:
    - Just serialize as XML objects

    Phase 2:
    - Runbook optimization
    """
    # Simple concatenation approach for MVP
    runbook_parts = []

    # Turn these into steps
    for msg in messages:
        # Extract role and content from message
        match msg.message:
            case ModelRequest(parts=parts):
                xml_parts = []
                for part in parts:
                    match part:
                        case UserPromptPart(content=content):
                            xml_parts.append(
                                f'<Step type="user-prompt">\n'
                                f'\t<Content type="json">{json.dumps(content, indent=2)}</Content>\n'
                                "</Step>\n"
                            )
                        case ToolReturnPart(tool_name=tool_name, content=content):
                            xml_parts.append(
                                f'<Step type="tool-return" tool_name="{tool_name}">\n'
                                f'\t<Content type="json">{json.dumps(content, indent=2)}</Content>\n'
                                "</Step>\n"
                            )
                content = "".join(xml_parts)
                runbook_parts.append(content)
            case ModelResponse(parts=parts):
                # Convert each part to XML
                xml_parts = []
                for part in parts:
                    match part:
                        case ToolCallPart(tool_name=tool_name, args=args):
                            xml_parts.append(
                                f'<Step type="tool-call" tool_name="{tool_name}">\n'
                                f'\t<Args type="json">{json.dumps(args, indent=2)}</Args>\n'
                                "</Step>\n"
                            )
                content = "".join(xml_parts)
                runbook_parts.append(content)
    return f'<Steps description="The steps that the analyst took to investigate or resolve the case">\n{"".join(runbook_parts)}\n</Steps>'


def _reduce_tools_to_text(tools: list[str]) -> str:
    """Reduce tools to a single text."""
    return f'<Tools description="The tools available to the agent for this runbook">\n{json.dumps(tools, indent=2)}\n</Tools>'


def _reduce_case_to_text(case: Case) -> str:
    """Reduce case to a single text."""
    return f'<Case description="The case that the analyst investigated or resolved">\n{yaml.safe_dump(case.model_dump(mode="json"))}\n</Case>'


def _reduce_messages_to_text(messages: list[ChatMessage], n: int = 4) -> str:
    """Reduce first n user or assistant messages to a single text."""
    messages_text = []
    for msg in messages[:n]:
        if isinstance(msg.message, ModelRequest):
            for part in msg.message.parts:
                if isinstance(part, UserPromptPart):
                    messages_text.append(f"User: {str(part.content)}")
        elif isinstance(msg.message, ModelResponse):
            for part in msg.message.parts:
                if isinstance(part, TextPart):
                    messages_text.append(f"Assistant: {str(part.content)}")
    return f'<Messages description="The messages between the user and the agent">\n{"\n".join(messages_text)}\n</Messages>'


def _reduce_runbook_to_text(runbook: Runbook) -> str:
    """Reduce runbook to a single text."""
    return f'<Runbook id="{runbook.id}" description="The runbook to be edited">\nversion={runbook.version}\n{runbook.instructions}\n</Runbook>'


class CaseToRunbookPrompts(BaseModel):
    """Prompts for building a runbook to case."""

    case: Case
    messages: list[ChatMessage]
    tools: list[str]

    @property
    def instructions(self) -> str:
        """Build the instructions for the runbook to case."""
        return textwrap.dedent("""
            You are an expert automation assistant for security and IT operations. Runbooks are used to automate actions on a case or ticket based on a set of instructions.
            You will be given a case with a summary, description, and payload inside the <Case> tag. You will be given a list of <Steps> that the analyst took to investigate or resolve the case.

            <Task>
            Produce a concise, generalized runbook suitable for similar future cases.
            </Task>

            <Sections description="The sections of the runbook">
            <Objective>
            - Generalized purpose; no case-specific identifiers or private values
            </Objective>

            <Steps>
            - Concise, actionable, generalized instructions
            - If a step clearly requires a tool call, include the tool ID in the step
            - Infer parameters from intent/structure; do not copy example inputs/outputs verbatim
            </Steps>

            <Tools>
            - The tools available to the agent for this runbook
            </Tools>
            </Sections>

            <PostProcessingRules>
            - Remove irrelevant, duplicate, or failed steps
            - Merge repeated patterns (loops over items) into one generalized step
            - Summarize each remaining step into a short, actionable instruction
            </PostProcessingRules>

            <GeneralizationRules>
            - Do NOT hardcode case-specific identifiers or values (e.g., case IDs, alert IDs, timestamps, file paths). Use placeholders (e.g., <case_id>) or JSONPath to the incoming <Case> (e.g., $.case.payload.host).
            - Specific values of entities (e.g. emails, IP addresses, hostnames, domains, URLs, hashes, usernames) MAY ONLY appear as "example" values in the Trigger or Steps section.
            - Do not copy example tool inputs/outputs verbatim; infer parameters from intent and structure.
            - Prefer criteria/patterns/field references over literal values.
            </GeneralizationRules>
        """)

    @property
    def user_prompt(self) -> str:
        """Build the user prompt for the runbook to case."""
        return textwrap.dedent(f"""
            {_reduce_case_to_text(self.case)}

            {_reduce_messages_to_steps(self.messages)}

            {_reduce_tools_to_text(self.tools)}
        """)


class CaseToRunbookTitlePrompts(BaseModel):
    """Prompts for generating a runbook title from a case."""

    case: Case
    messages: list[ChatMessage]
    n_messages: int = 4

    @property
    def instructions(self) -> str:
        """Build the instructions for generating a runbook title."""
        return textwrap.dedent("""
            You are an expert ITSM runbook title specialist.
            Generate a precise 4-7 word title for this automation runbook.
            Focus on the action/resolution being automated.
            Use standard ITSM terminology (e.g., Investigate, Remediate, Configure, Deploy).
        """)

    @property
    def user_prompt(self) -> str:
        """Build the user prompt for generating a runbook title."""
        return textwrap.dedent(f"""
            {_reduce_case_to_text(self.case)}

            {_reduce_messages_to_text(self.messages, n=self.n_messages)}
        """)


class RunbookCopilotPrompts(BaseModel):
    """Prompts for creating or editing a runbook from a user request."""

    runbook: Runbook | None = None

    @property
    def instructions(self) -> str:
        """Build the instructions for creating or editing a runbook from a request."""

        if self.runbook:
            action = "edit"
            source = "You will be given a user request to edit the runbook. The current runbook to be edited will be provided to you inside the <Runbook> tag."
            runbook_id = f"<RunbookID>{self.runbook.id}</RunbookID>"
        else:
            action = "create"
            source = "You will be given a user request to create a runbook that will be used to automate a specific task."
            runbook_id = ""

        base_instructions = textwrap.dedent(f"""
            You are an expert runbook {action} agent. {source}
            You must interpret the user's request and {action} the runbook as requested by the user.
            Use the runbook tools provided to you to {action} the runbook.

            {runbook_id}

            <RunbookSections>
            - The runbook has the following sections: Objective, Tools, Steps

            <EditingRules>
            - You must ask clarifying questions to the user if you are not sure about the user's intent.
            - If you've determined you have to update the Runbook instructions, you should update the runbook `instructions` using Markdown.
            - You will be given tools to help assist you in {action} the runbook.
            </EditingRules>
        """)

        if self.runbook:
            return f"{base_instructions}\n\n{_reduce_runbook_to_text(self.runbook)}".strip()
        return base_instructions.strip()

    @property
    def user_prompt(self) -> str:
        """Build the user prompt for creating or editing a runbook from a request."""
        raise NotImplementedError(
            "User prompt is not implemented for runbook copilot prompts"
        )


class ExecuteRunbookPrompts(BaseModel):
    """Prompts for executing a runbook on a case."""

    runbook: Runbook
    case: Case

    @property
    def instructions(self) -> str:
        """Build the instructions for executing a runbook."""
        return textwrap.dedent("""
            You are an expert case / ticket automation assistant. You will be given a runbook, which is a set of instructions to execute on a specific case / ticket.

            <StepHandling>
            - user-prompt: follow the instruction
            - tool-call: use the named tool; infer inputs from <Case> and prior returns
                - You *MUST NOT* reuse hardcoded or example values. You *MUST* derive fresh values from <Case>
                - For example, if the <Case> has a hostname `example.com`, during tool calls you *MUST NOT* use `example.com` as the hostname. You *MUST* use the actual hostname from the <Case>
            - tool-return: note the type/shape, not literal example values
            </StepHandling>

            <Rules>
            1. Call tools only when a <Steps> says so
            2. Preserve the original <Step> order
            3. *NEVER reuse hardcoded or example inputs/outputs*; derive fresh values from <Case>. Doing so means you are not executing the runbook on the incoming <Case>.
            4. No conversational chatter, rationale, or chain-of-thought; keep outputs minimal and task-focused
            5. You should first read the case content and the <Case> to determine if it is relevant to the <Steps>. Only if the case is clearly unrelated to these <Steps>, stop and output INAPPLICABLE, with an explanation.
            6. Do not restate or summarize <Case> or <Steps>
            7. Keep each message under ~150 tokens; do not dump large payloads; reference them instead
            </Rules>
        """)

    @property
    def user_prompt(self) -> str:
        """Build the user prompt for executing a runbook."""
        return textwrap.dedent(f"""
            {self.instructions}

            <RunbookID>
            {self.runbook.id}
            </RunbookID>

            {_reduce_runbook_to_text(self.runbook)}

            {_reduce_case_to_text(self.case)}
        """)
