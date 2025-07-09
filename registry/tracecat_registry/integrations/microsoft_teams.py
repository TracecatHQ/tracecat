import json
import uuid
from typing import Annotated, Any, Literal
from typing_extensions import Doc

import httpx

from tracecat_registry import RegistryOAuthSecret, registry, secrets


microsoft_teams_ac_oauth_secret = RegistryOAuthSecret(
    provider_id="microsoft_teams",
    grant_type="authorization_code",
)
"""Microsoft Teams OAuth2.0 credentials (Authorization Code grant).

- name: `microsoft_teams`
- provider_id: `microsoft_teams`
- token_name: `MICROSOFT_TEAMS_[SERVICE|USER]_TOKEN`
"""

TeamId = Annotated[str, Doc("The ID of the team.")]
ChannelId = Annotated[str, Doc("The ID of the channel.")]
MessageId = Annotated[str, Doc("The ID of the message.")]
OptionalTeamId = Annotated[str | None, Doc("Team ID for context.")]
OptionalChannelId = Annotated[str | None, Doc("Channel ID for context.")]

OptionalTitle = Annotated[str | None, Doc("Title for the card.")]
RequiredTitle = Annotated[str, Doc("Title/button text.")]
OptionalSubtitle = Annotated[str | None, Doc("Subtitle for the card.")]
CardElements = Annotated[
    list[dict[str, Any]],
    Doc("List of Adaptive Card elements."),
]

TaskModuleSize = Annotated[
    int | str,
    Doc("Task module dimensions (small/medium/large or pixels)."),
]

AdaptiveCardSpacing = Annotated[
    Literal["None", "Small", "Default", "Medium", "Large", "ExtraLarge"] | None,
    Doc("Spacing above the element."),
]
AdaptiveCardSeparator = Annotated[
    bool,
    Doc("Whether to show a separator line above."),
]


@registry.register(
    default_title="Send Teams message",
    description="Send a message to a Microsoft Teams channel.",
    display_group="Microsoft Teams",
    doc_url="https://learn.microsoft.com/en-us/graph/api/channel-post-messages",
    namespace="tools.microsoft_teams",
    secrets=[microsoft_teams_ac_oauth_secret],
)
async def send_teams_message(
    team_id: TeamId,
    channel_id: ChannelId,
    message: Annotated[str, Doc("The message to send.")],
) -> dict[str, str]:
    token = secrets.get(microsoft_teams_ac_oauth_secret.token_name)

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Microsoft Graph API endpoint for sending channel messages
    url = f"https://graph.microsoft.com/v1.0/teams/{team_id}/channels/{channel_id}/messages"

    # Message payload
    payload = {"body": {"content": message}}

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Reply to a Teams message",
    description="Send a reply to a Microsoft Teams message.",
    display_group="Microsoft Teams",
    doc_url="https://learn.microsoft.com/en-us/graph/api/channel-post-replies",
    namespace="tools.microsoft_teams",
    secrets=[microsoft_teams_ac_oauth_secret],
)
async def reply_teams_message(
    team_id: TeamId,
    channel_id: ChannelId,
    message_id: MessageId,
    message: Annotated[str, Doc("The message to send.")],
) -> dict[str, Any]:
    token = secrets.get(microsoft_teams_ac_oauth_secret.token_name)

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Microsoft Graph API endpoint for sending channel messages
    url = f"https://graph.microsoft.com/v1.0/teams/{team_id}/channels/{channel_id}/messages/{message_id}/replies"

    # Message payload
    payload = {"body": {"content": message}}

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Create Teams channel",
    description="Create a new channel in a Microsoft Teams team. Can create either public (standard) or private channels.",
    display_group="Microsoft Teams",
    doc_url="https://learn.microsoft.com/en-us/graph/api/channel-post",
    namespace="tools.microsoft_teams",
    secrets=[microsoft_teams_ac_oauth_secret],
)
async def create_teams_channel(
    team_id: TeamId,
    display_name: Annotated[
        str,
        Doc("The display name for the channel."),
    ],
    description: Annotated[
        str | None,
        Doc("Description for the channel."),
    ] = None,
    is_private: Annotated[
        bool,
        Doc("Whether to create a private channel (requires members)."),
    ] = False,
    owner_user_ids: Annotated[
        list[str] | None,
        Doc("List of user IDs to add as owners (required for private channels)."),
    ] = None,
) -> dict[str, str]:
    """Create a Teams channel.

    For private channels, at least one owner must be specified in owner_user_ids.
    """
    token = secrets.get(microsoft_teams_ac_oauth_secret.token_name)

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    url = f"https://graph.microsoft.com/beta/teams/{team_id}/channels"

    if is_private:
        if not owner_user_ids:
            raise ValueError(
                "Private channels require at least one owner in owner_user_ids"
            )

        members = []
        for user_id in owner_user_ids:
            members.append(
                {
                    "@odata.type": "#microsoft.graph.aadUserConversationMember",
                    "roles": ["owner"],
                    "user@odata.bind": f"https://graph.microsoft.com/beta/users('{user_id}')",
                }
            )

        payload = {
            "@odata.type": "#Microsoft.Graph.channel",
            "membershipType": "private",
            "displayName": display_name,
            "description": description or "",
            "members": members,
        }
    else:
        payload = {
            "displayName": display_name,
            "description": description or "",
            "membershipType": "standard",
        }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="List channel messages",
    description="Retrieve the list of messages (without the replies) in a channel of a team.",
    display_group="Microsoft Teams",
    doc_url="https://learn.microsoft.com/en-us/graph/api/channel-list-messages?view=graph-rest-beta&tabs=python",
    namespace="tools.microsoft_teams",
    secrets=[microsoft_teams_ac_oauth_secret],
)
async def list_channel_messages(
    team_id: TeamId,
    channel_id: ChannelId,
    top: Annotated[
        int | None,
        Doc("Number of messages to return per page (default 20, max 50)."),
    ] = None,
    expand_replies: Annotated[
        bool,
        Doc("Whether to expand replies for each message."),
    ] = False,
) -> dict[str, str]:
    """List messages from a Teams channel.

    Note: This API requires ChannelMessage.Read.All or ChannelMessage.Read.Group permissions.
    """
    token = secrets.get(microsoft_teams_ac_oauth_secret.token_name)

    headers = {"Authorization": f"Bearer {token}"}

    url = f"https://graph.microsoft.com/beta/teams/{team_id}/channels/{channel_id}/messages"

    params = {}
    if top:
        if top > 50:
            raise ValueError("Top parameter cannot exceed 50 messages per page")
        params["$top"] = top

    if expand_replies:
        params["$expand"] = "replies"

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Delete Teams channel",
    description="Delete a channel from a Microsoft Teams team.",
    display_group="Microsoft Teams",
    doc_url="https://learn.microsoft.com/en-us/graph/api/channel-delete?view=graph-rest-beta&tabs=http",
    namespace="tools.microsoft_teams",
    secrets=[microsoft_teams_ac_oauth_secret],
)
async def delete_teams_channel(
    team_id: TeamId,
    channel_id: ChannelId,
) -> dict[str, Any]:
    """Delete a Teams channel.

    Note: This API requires Channel.Delete.All or Channel.Delete.Group permissions.
    """
    token = secrets.get(microsoft_teams_ac_oauth_secret.token_name)

    headers = {"Authorization": f"Bearer {token}"}

    url = f"https://graph.microsoft.com/beta/teams/{team_id}/channels/{channel_id}"

    async with httpx.AsyncClient() as client:
        response = await client.delete(url, headers=headers)
        response.raise_for_status()

        return {
            "success": True,
            "status_code": response.status_code,
            "team_id": team_id,
            "channel_id": channel_id,
            "message": "Channel deleted successfully",
        }


@registry.register(
    default_title="Format fact set",
    description="Format Docs into an Adaptive Card FactSet (key-value pairs).",
    display_group="Microsoft Teams",
    doc_url="https://adaptivecards.io/explorer/FactSet.html",
    namespace="tools.microsoft_teams",
    secrets=[microsoft_teams_ac_oauth_secret],
)
def format_fact_set(
    facts: Annotated[
        list[dict[str, str]],
        Doc(
            "List of JSONs with `title` and `value` keys."
            " E.g. `[{'title': 'Status', 'value': 'Critical'}, {'title': 'Priority', 'value': 'High'}]`."
        ),
    ],
    spacing: AdaptiveCardSpacing = None,
    separator: AdaptiveCardSeparator = False,
) -> dict[str, Any]:
    """Create an Adaptive Card FactSet element."""
    fact_set = {
        "type": "FactSet",
        "facts": [{"title": fact["title"], "value": fact["value"]} for fact in facts],
    }

    if spacing:
        fact_set["spacing"] = spacing
    if separator:
        fact_set["separator"] = separator

    return fact_set


@registry.register(
    default_title="Format text block",
    description="Format text into an Adaptive Card TextBlock.",
    display_group="Microsoft Teams",
    doc_url="https://adaptivecards.io/explorer/TextBlock.html",
    namespace="tools.microsoft_teams",
    secrets=[microsoft_teams_ac_oauth_secret],
)
def format_text_block(
    text: Annotated[str, Doc("The text content.")],
    size: Annotated[
        Literal["Small", "Default", "Medium", "Large", "ExtraLarge"] | None,
        Doc("Text size."),
    ] = None,
    weight: Annotated[
        Literal["Lighter", "Default", "Bolder"] | None,
        Doc("Text weight."),
    ] = None,
    color: Annotated[
        Literal["Default", "Dark", "Light", "Accent", "Good", "Warning", "Attention"]
        | None,
        Doc("Text color."),
    ] = None,
    is_subtle: Annotated[
        bool | None,
        Doc("Whether text is subtle."),
    ] = None,
    wrap: Annotated[bool, Doc("Whether text should wrap.")] = True,
    spacing: AdaptiveCardSpacing = None,
    separator: AdaptiveCardSeparator = False,
) -> dict[str, Any]:
    """Create an Adaptive Card TextBlock element."""
    text_block = {
        "type": "TextBlock",
        "text": text,
        "wrap": wrap,
    }

    if size:
        text_block["size"] = size
    if weight:
        text_block["weight"] = weight
    if color:
        text_block["color"] = color
    if is_subtle is not None:
        text_block["isSubtle"] = is_subtle
    if spacing:
        text_block["spacing"] = spacing
    if separator:
        text_block["separator"] = separator

    return text_block


@registry.register(
    default_title="Format action set",
    description="Format buttons into an Adaptive Card ActionSet.",
    display_group="Microsoft Teams",
    doc_url="https://adaptivecards.io/explorer/ActionSet.html",
    namespace="tools.microsoft_teams",
    secrets=[microsoft_teams_ac_oauth_secret],
)
def format_action_set(
    actions: Annotated[
        list[dict[str, Any]],
        Doc(
            "List of action objects. Each action should have 'type' and 'title' keys."
            " For messageBack: `{'type': 'messageBack', 'title': 'Click me', 'text': 'clicked', 'value': 'data'}`"
            " For openUrl: `{'type': 'openUrl', 'title': 'Open', 'url': 'https://example.com'}`"
        ),
    ],
    spacing: AdaptiveCardSpacing = None,
    separator: AdaptiveCardSeparator = False,
) -> dict[str, Any]:
    """Create an Adaptive Card ActionSet element."""
    formatted_actions = []

    for action in actions:
        if action["type"] == "messageBack":
            formatted_action = {
                "type": "messageBack",
                "title": action["title"],
                "text": action.get("text", action["title"]),
                "displayText": action.get("displayText", action["title"]),
                "value": action.get("value", action["title"]),
            }
        elif action["type"] == "openUrl":
            formatted_action = {
                "type": "openUrl",
                "title": action["title"],
                "url": action["url"],
            }
        else:
            # Pass other action types through as-is
            formatted_action = action

        formatted_actions.append(formatted_action)

    action_set = {
        "type": "ActionSet",
        "actions": formatted_actions,
    }

    if spacing:
        action_set["spacing"] = spacing
    if separator:
        action_set["separator"] = separator

    return action_set


@registry.register(
    default_title="Format choice set",
    description="Format a dropdown/choice input into an Adaptive Card Input.ChoiceSet.",
    display_group="Microsoft Teams",
    doc_url="https://adaptivecards.io/explorer/Input.ChoiceSet.html",
    namespace="tools.microsoft_teams",
    secrets=[microsoft_teams_ac_oauth_secret],
)
def format_choice_set(
    id: Annotated[str, Doc("Unique identifier for the input.")],
    choices: Annotated[
        list[dict[str, str]],
        Doc(
            "List of choice objects with 'title' and 'value' keys."
            " E.g. `[{'title': 'Option 1', 'value': 'opt1'}, {'title': 'Option 2', 'value': 'opt2'}]`"
        ),
    ],
    label: Annotated[
        str | None,
        Doc("Label for the choice set."),
    ] = None,
    placeholder: Annotated[
        str | None,
        Doc("Placeholder text."),
    ] = None,
    value: Annotated[
        str | None,
        Doc("Default selected value."),
    ] = None,
    is_multi_select: Annotated[
        bool,
        Doc("Whether multiple choices can be selected."),
    ] = False,
    style: Annotated[
        Literal["compact", "expanded"] | None,
        Doc("Presentation style for the choices."),
    ] = None,
    spacing: AdaptiveCardSpacing = None,
    separator: AdaptiveCardSeparator = False,
) -> dict[str, Any]:
    """Create an Adaptive Card Input.ChoiceSet element."""
    choice_set = {
        "type": "Input.ChoiceSet",
        "id": id,
        "choices": [
            {"title": choice["title"], "value": choice["value"]} for choice in choices
        ],
        "isMultiSelect": is_multi_select,
    }

    if label:
        choice_set["label"] = label
    if placeholder:
        choice_set["placeholder"] = placeholder
    if value:
        choice_set["value"] = value
    if style:
        choice_set["style"] = style
    if spacing:
        choice_set["spacing"] = spacing
    if separator:
        choice_set["separator"] = separator

    return choice_set


@registry.register(
    default_title="Send Teams buttons",
    description="Send buttons/ActionSet as a thumbnail card to a Microsoft Teams channel.",
    display_group="Microsoft Teams",
    doc_url="https://learn.microsoft.com/en-us/graph/api/channel-post-messages",
    namespace="tools.microsoft_teams",
    secrets=[microsoft_teams_ac_oauth_secret],
)
async def send_teams_buttons(
    team_id: TeamId,
    channel_id: ChannelId,
    action_set: Annotated[
        dict[str, Any],
        Doc("ActionSet element from format_action_set function."),
    ],
    title: OptionalTitle = None,
    subtitle: OptionalSubtitle = None,
    text: Annotated[
        str | None,
        Doc("Text content for the button card."),
    ] = None,
) -> dict[str, Any]:
    """Send ActionSet buttons as a Teams thumbnail card."""
    token = secrets.get(microsoft_teams_ac_oauth_secret.token_name)

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    url = f"https://graph.microsoft.com/v1.0/teams/{team_id}/channels/{channel_id}/messages"

    attachment_id = str(uuid.uuid4()).replace("-", "")

    # Convert ActionSet to thumbnail card buttons
    buttons = []
    if action_set.get("type") == "ActionSet":
        actions = action_set.get("actions", [])
        for action in actions:
            if action.get("type") == "messageBack":
                buttons.append(
                    {
                        "type": "messageBack",
                        "title": action.get("title"),
                        "text": action.get("text"),
                        "displayText": action.get("displayText"),
                        "value": action.get("value"),
                    }
                )
            elif action.get("type") == "openUrl":
                buttons.append(
                    {
                        "type": "openUrl",
                        "title": action.get("title"),
                        "url": action.get("url"),
                    }
                )

    # Build thumbnail card content
    card_content = {}
    if title:
        card_content["title"] = title
    if subtitle:
        card_content["subtitle"] = subtitle
    if text:
        card_content["text"] = text
    if buttons:
        card_content["buttons"] = buttons

    # Build payload
    payload = {
        "body": {
            "contentType": "html",
            "content": f'<attachment id="{attachment_id}"></attachment>',
        },
        "attachments": [
            {
                "id": attachment_id,
                "contentType": "application/vnd.microsoft.card.thumbnail",
                "content": json.dumps(card_content),
            }
        ],
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Send adaptive card",
    description="Send a message with Adaptive Cards to a Microsoft Teams channel.",
    display_group="Microsoft Teams",
    doc_url="https://learn.microsoft.com/en-us/graph/api/channel-post-messages",
    namespace="tools.microsoft_teams",
    secrets=[microsoft_teams_ac_oauth_secret],
)
async def send_adaptive_card(
    team_id: TeamId,
    channel_id: ChannelId,
    card_elements: CardElements,
    title: OptionalTitle = None,
) -> dict[str, Any]:
    """Send an Adaptive Card message to Teams (without ActionSet elements)."""
    token = secrets.get(microsoft_teams_ac_oauth_secret.token_name)

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    url = f"https://graph.microsoft.com/v1.0/teams/{team_id}/channels/{channel_id}/messages"

    attachment_id = str(uuid.uuid4()).replace("-", "")

    # Build Adaptive Card structure (no ActionSet conversion)
    card_content = {
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": card_elements,
    }

    # Build payload
    payload = {
        "body": {
            "contentType": "html",
            "content": f'<attachment id="{attachment_id}"></attachment>',
        },
        "attachments": [
            {
                "id": attachment_id,
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": json.dumps(card_content),
            }
        ],
    }

    if title:
        payload["subject"] = title

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()
