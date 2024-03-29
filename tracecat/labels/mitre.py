import os
from pathlib import Path

MITRE_DIR = Path(os.path.expanduser("~/.tracecat"))


def to_lower_snake_case(name: str) -> str:
    name = name.lower().replace(" ", "_")
    return name


def get_mitre_tactics_techniques():
    return [
        "initial_access",
        "execution",
        "persistence",
        "privilege_escalation",
        "defense_evasion",
        "credential_access",
        "discovery",
        "lateral_movement",
        "collection",
        "exfiltration",
        "command_and_control",
        "impact",
    ]
