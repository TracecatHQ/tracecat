from typing import Any

from tracecat.ee.interactions.models import InteractionInput


def parse_slack_interaction_input(payload: dict[str, Any]) -> InteractionInput:
    """Extract event payload from various Slack payloads"""
    match payload:
        case {
            "message": {
                "metadata": {
                    "event_type": "interaction_created",
                    "event_payload": {
                        "interaction": {
                            "interaction_id": interaction_id,
                            "execution_id": execution_id,
                            "action_ref": action_ref,
                        }
                    },
                }
            },
        }:
            return InteractionInput(
                interaction_id=interaction_id,
                execution_id=execution_id,
                action_ref=action_ref,
                data=payload,
            )
        case _:
            raise ValueError("Invalid Slack payload")
