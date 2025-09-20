"""Service layer for runbook-related prompts."""

import textwrap
from typing import Any

import yaml

RUNBOOK_REQUIREMENTS = textwrap.dedent("""
    <Requirements>
    - Output ONLY the runbook as Markdown; no prose before/after
    - Do NOT wrap the entire runbook in a single code block
    - Use Markdown: headers (#, ##), lists (-), bold (**)
    - Use fenced blocks only for actual code/commands
    - Include sections: Objective, Tools, Trigger, Steps
    - Objective must be generalized (no specific IDs/private values)
    - Tools are provided as <Tools>
    - Trigger section (Markdown):
        - Start with a single line: **Execute when**:
        - Then list 1 to 3 concise, human-readable conditions as bullets, each referencing Alert fields via JSONPath or placeholders
        - Add a **Do not execute when** subsection (optional, 1-3 bullet points) listing clear exclusions
        - Keep the language human-readable; avoid raw code where possible. Use JSONPath only to point to specific fields
    </Requirements>
""")


def create_case_to_runbook_prompt(case: dict[str, Any], steps: str) -> str:
    """Create a case to runbook prompt.

    Args:
        case: The case information that was investigated or resolved
        steps: The steps taken by the analyst

    Returns:
        The formatted prompt string
    """
    return textwrap.dedent(f"""
        You are an expert automation assistant for security and IT operations. Runbooks are used to automate actions on a case or ticket based on a set of instructions.
        You will be given a case with a summary, description, and payload inside the <Case> tag. You will be given a list of <Steps> that the analyst took to investigate or resolve the case.

        <Task>
        Produce a concise, generalized runbook suitable for similar future cases.
        </Task>

        Here is the <Case> that the analyst investigated or resolved:
        {yaml.safe_dump(case)}

        Here are the <Steps> that the analyst took to investigate or resolve the case:
        {steps}

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

        {RUNBOOK_REQUIREMENTS}
    """)


def create_request_to_runbook_prompt() -> str:
    """Create a request to runbook prompt.

    Returns:
        The formatted prompt string
    """
    return textwrap.dedent("""
        You are an expert runbook creation agent. You will be given a user request to create a runbook that will be used to automate a specific case or ticket.

        <Task>
        Your task is to interpret the users intent and create a runbook as requested by the user.
        </Task>

        <EditingRules>
        - You must ask clarifying questions to the user if you are not sure about the user's intent.
        - You will be given tools to help assist you in creating the runbook.
        </EditingRules>

        {RUNBOOK_REQUIREMENTS}
    """)


def create_execute_runbook_prompt(steps: str) -> str:
    """Create an execute runbook prompt.

    Args:
        steps: The steps to execute on the case/ticket

    Returns:
        The formatted prompt string
    """
    return textwrap.dedent(f"""
        You are an expert case / ticket automation assistant. You will be given a runbook, which is a set of instructions to execute on a specific case / ticket.

        Here are the <Steps> to execute on the case / ticket:
        {steps}

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
