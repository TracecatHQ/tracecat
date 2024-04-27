"""Integrations with Sublime Platform API.

Required credentials: `sublime` secret with `SUBLIME_API_KEY` key
and optional `SUBLIME_BASE_URL` key using a self-hosted instance of Sublime.
"""

import os
from typing import Any

import httpx
from tenacity import retry, stop_after_delay, wait_combine, wait_fixed

from tracecat.integrations._registry import registry

SUBLIME_BASE_URL = "https://api.platform.sublimesecurity.com"


def create_sublime_client(app_name: str | None = None) -> httpx.Client:
    api_key = os.environ["SUBLIME_API_KEY"]
    app_name = app_name or "client"
    headers = {"User-Agent": f"tracecat/{app_name}", "Key": api_key}
    return httpx.Client(base_url=SUBLIME_BASE_URL, headers=headers)


@retry(wait=wait_combine(wait_fixed(2), wait_fixed(10)), stop=stop_after_delay(120))
def get_sublime_result(
    endpoint: str, app_name: str | None = None, params: dict[str, Any] = None
) -> dict[str, Any]:
    with create_sublime_client(app_name) as client:
        rsp = client.get(endpoint, params=params)
        if rsp.status_code == 200:
            return rsp.json()
        else:
            rsp.raise_for_status()


# Binexplode API


@registry.register(
    description="Explode a binary file and get results", secrets=["sublime"]
)
def explode_binary(
    file_contents: str, file_name: str, app_name: str | None = None
) -> dict[str, Any]:
    """Explodes a binary file and returns the results.

    First posts file to binexplode, then polls the result (until it is ready).

    API references:
    - https://docs.sublimesecurity.com/reference/postscan-1
    - https://docs.sublimesecurity.com/reference/getscan-1
    """
    with create_sublime_client(app_name) as client:
        rsp = client.post(
            "v0/binexplode/scan",
            json={"file_contents": file_contents, "file_name": file_name},
        )
        rsp.raise_for_status()
        task_id = rsp.json().get("task_id")

    result = get_sublime_result(f"v0/binexplode/scan/{task_id}")
    return result


# Message Groups API
# NOTE: Sublime supports a "bulk" CRUD and single CRUD endpoint
# for operations on message groups. We always pick the bulk endpoint if available.


@registry.register(description="Hunt for messages using MQL", secrets=["sublime"])
def hunt_messages(
    query: str,
    # start: str | None = None,  # TODO: Replace with datetime
    # end: str | None = None,  # TODO: Replace with datetime
    app_name: str | None = None,
) -> dict[str, Any]:
    """Hunt using MQL (Message Query Language) to find matching message groups.

    API references:
    - https://docs.sublimesecurity.com/reference/huntmessagegroups-1
    - https://docs.sublimesecurity.com/reference/gethuntresults-1
    """

    with create_sublime_client(app_name) as client:
        rsp = client.post(
            "v0/message-groups/hunt",
            json={
                "source": query,
                # "created_at[gte]": start,
                # "created_at[lt]": end,
            },
        )
        rsp.raise_for_status()
        task_id = rsp.json().get("task_id")

    result = get_sublime_result(f"v0/message-groups/hunt/{task_id}")
    return result


@registry.register(description="Classify message groups", secrets=["sublime"])
def classify_messages(
    group_ids: list[str], classification: str, app_name: str | None = None
) -> list[str]:
    """Classify multiple message groups given a list of group IDs as:
    - `malicious`
    - `benign`
    - `unwanted`
    - `simulation`
    - `skip`

    Returns list of group IDs.

    API references:
    - https://docs.sublimesecurity.com/reference/reviewmessagegroups
    """
    # NOTE: there are other params that can be used
    # - action
    # - custom_action_ids
    # - review_comment
    # But there isn't much documentation on what are allowed "actions"
    with create_sublime_client(app_name) as client:
        rsp = client.post(
            "v0/message-groups/review",
            json={
                "classification": classification,
                "message_group_ids": group_ids,
            },
        )
        rsp.raise_for_status()
    return group_ids


@registry.register(description="Dismiss messages", secrets=["sublime"])
def dismiss_messages(group_ids: list[str], app_name: str | None = None) -> list[str]:
    """Dismiss all messages in multiple groups, including future messages.
    Returns list of group IDs.

    API references:
    - https://docs.sublimesecurity.com/reference/dismissmultiplemessagegroups-1
    """
    with create_sublime_client(app_name) as client:
        rsp = client.post(
            "v0/message-groups/dismiss",
            json={"message_group_ids": group_ids},
        )
        rsp.raise_for_status()
    return group_ids


@registry.register(description="Quarantine messages", secrets=["sublime"])
def quarantine_messages(
    group_ids: list[str], wait_for_completion: bool = False, app_name: str | None = None
) -> dict[str, Any] | str:
    """Quarantine all messages in multiple groups, including future messages.

    If `wait_for_completion` is True, waits for the operation to complete,
    and returns task object. Otherwise, return `task_id` without waiting.
    """
    with create_sublime_client(app_name) as client:
        rsp = client.post(
            "v0/message-groups/quarantine",
            json={"message_group_ids": group_ids},
        )
        rsp.raise_for_status()
    task_id = rsp.json().get("task_id")
    if wait_for_completion:
        return get_sublime_result(f"v0/tasks/{task_id}")
    return task_id


@registry.register(description="Trash messages", secrets=["sublime"])
def trash_messages(
    group_ids: list[str], wait_for_completion: bool = False, app_name: str | None = None
) -> dict[str, Any] | str:
    """Trash all messages in multiple groups, including future messages.

    If `wait_for_completion` is True, waits for the operation to complete,
    and returns task object. Otherwise, return `task_id` without waiting.
    """
    with create_sublime_client(app_name) as client:
        rsp = client.post(
            "v0/message-groups/trash",
            json={"message_group_ids": group_ids},
        )
        rsp.raise_for_status()
    task_id = rsp.json().get("task_id")
    if wait_for_completion:
        return get_sublime_result(f"v0/tasks/{task_id}")
    return task_id


# Message API
# NOTE: Used for fine-grained control over a single message


@registry.register(description="Create message", secrets=["sublime"])
def create_message(raw_message: str, app_name: str | None = None) -> dict[str, Any]:
    """Create then retrieve message data model from a base64 encoded EML message.

    API references:
    - https://docs.sublimesecurity.com/reference/createmessage
    """
    with create_sublime_client(app_name) as client:
        rsp = client.post(
            "v0/messages/create",
            json={"raw_message": raw_message},
        )
        rsp.raise_for_status()
        message_model = rsp.json()
    return message_model


@registry.register(description="Analyze message", secrets=["sublime"])
def analyze_message(
    raw_message: str,
    queries: list[dict[str, str | bool]] | None = None,
    app_name: str | None = None,
) -> dict[str, Any]:
    """Create then analyze message data model from a base64 encoded EML message.

    `queries` is a list of objects with fields:
    - [Required] `source` (str): MQL source to run against the message
    - [Optional] `name` (bool): whether the rule is active
    - [Optional] `severity`(str or null): severity associated with the rule

    If `queries` is not specified, all active detection rules and insights are run by default.

    API references:
    - https://docs.sublimesecurity.com/reference/analyzemessagebyid
    """
    # TODO: this API has both `rules` and `queries` as parameters
    # Need to clarify the difference between them...they seem mostly similar
    with create_sublime_client(app_name) as client:
        # Create message model
        rsp = client.post(
            "v0/messages/create",
            json={"raw_message": raw_message},
        )
        rsp.raise_for_status()
        message_id = rsp.json().get("id")
        # Analyze message
        if queries is None:
            rsp = client.post(
                f"v0/messages/{message_id}/analyze",
                json={"run_active_detection_rules": True, "run_all_insights": True},
            )
        else:
            rsp = client.post(
                f"v0/messages/{message_id}/analyze",
                json={"queries": queries},
            )
        # NOTE: returns a object with fields `query_results` array of objects
        # and `rule_results` array of objects
        results = rsp.json()

    return results


@registry.register(description="Score existing message given ID", secrets=["sublime"])
def score_message(
    message_id: str, app_name: str | None = None
) -> dict[str, dict[str, Any]]:
    """Score an existing message given its ID.

    API references:
    - https://docs.sublimesecurity.com/reference/attackscoreformessage
    """
    with create_sublime_client(app_name) as client:
        rsp = client.get(f"v0/messages/{message_id}/attack_score")
        rsp.raise_for_status()
    return rsp.json()


@registry.register(description="Restore trashed message given ID", secrets=["sublime"])
def restore_message(
    message_id: str, wait_for_completion: bool = False, app_name: str | None = None
) -> str | dict[str, Any]:
    """Restores trashed message.

    If `wait_for_completion` is True, waits for the operation to complete and returns task object.
    Otherwise, return `task_id` without waiting.

    Often used in response to a Sublime webhook alert:
    https://docs.sublimesecurity.com/docs/webhooks

    API references:
    - https://docs.sublimesecurity.com/reference/restoremessage-1
    """
    with create_sublime_client(app_name) as client:
        rsp = client.post(f"v0/messages/{message_id}/restore")
        rsp.raise_for_status()
        task_id = rsp.json().get("task_id")
    if wait_for_completion:
        return get_sublime_result(f"v0/tasks/{task_id}")
    return task_id


@registry.register(description="Trash message", secrets=["sublime"])
def trash_message(
    message_id: str, wait_for_completion: bool = False, app_name: str | None = None
) -> str | dict[str, Any]:
    """Trash message.

    If `wait_for_completion` is True, waits for the operation to complete and returns task object.
    Otherwise, return `task_id` without waiting.

    Often used in response to a Sublime webhook alert:
    https://docs.sublimesecurity.com/docs/webhooks

    API references:
    - https://docs.sublimesecurity.com/reference/trashmessage-1
    """
    with create_sublime_client(app_name) as client:
        rsp = client.post(f"v0/messages/{message_id}/trash")
        rsp.raise_for_status()
        task_id = rsp.json().get("task_id")
    if wait_for_completion:
        return get_sublime_result(f"v0/tasks/{task_id}")
    return task_id


# User Reports API


@registry.register(description="List user phishing reports", secrets=["sublime"])
def list_user_reports(
    limit: int | None = None,
    # start: str | None = None,  # TODO: Replace with datetime
    # end: str | None = None,  # TODO: Replace with datetime
    app_name: str | None = None,
) -> dict[str, Any]:
    """List all user phishing reports.

    API references:
    - https://docs.sublimesecurity.com/reference/listuserreports
    """
    with create_sublime_client(app_name) as client:
        rsp = client.get(
            "v0/user-reports",
            params={
                "limit": limit,
                # "reported_at[gte]": start,
                # "reported_at[lt]": end,
            },
        )
        rsp.raise_for_status()
    return rsp.json()
