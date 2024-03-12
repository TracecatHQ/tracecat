import os
from pathlib import Path

import polars as pl
from attackcti import attack_client

MITRE_DIR = Path("~/.tracecat")


def to_lower_snake_case(name: str) -> str:
    name = name.lower().replace(" ", "_")
    return name


def get_mitre_tactics_techniques():
    path = MITRE_DIR / "mitre_ttp.csv"
    if os.path.exists(path):
        return pl.read_csv(path).get_column("ttp").to_list()

    client = attack_client()
    techniques = client.get_enterprise_techniques()
    tactic_techniques = [
        to_lower_snake_case(f"{tactic['phase_name']}.{technique['name']}")
        for technique in techniques
        for tactic in technique["kill_chain_phases"]
    ]

    pl.DataFrame({"ttp": tactic_techniques}).write_csv(path)
    return tactic_techniques
