"""Tracecat utility functions."""

from uuid import uuid4

from slugify import slugify


def gen_id(prefix: str):
    separator = "-"

    def wrapper():
        return prefix + separator + uuid4().hex

    return wrapper


def get_ref(text: str) -> str:
    """Return a slugified version of the text."""
    return slugify(text, separator="_")


def action_key(wfid: str, ref: str) -> str:
    """Inner workflow ID for an action, using the workflow ID and action ref."""
    return f"{wfid}:{ref}"
