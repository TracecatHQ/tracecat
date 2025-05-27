"""Artifacts produced by an AI agent.

Thinking
---
I quite like the artifact <antml:scope> syntax that anthropic uses for claude.
These markup tags get transformed into visual elements in the chat UI.

I'd like to have a similar syntax for tracecat.
Tracecat markup language -- TCML

Examples
```tcml
# Function call
<tcml:function_calls>
<tcml:invoke name="web_search">
<tcml:parameter name="query">semiconductor export restrictions 2025</tcml:parameter>
</tcml:invoke>
</tcml:function_calls>

# Draggable action node
<tcml:action>
<tcml:invoke type="core.transform.reshape">
<tcml:argument name="value" type="application/json">
{
    "shape": [10, 10]
}
</tcml:argument>
</tcml:invoke>
</tcml:action>
```


They also use


"""

from pydantic import BaseModel


class Artifact(BaseModel):
    """An artifact produced by an AI agent."""
