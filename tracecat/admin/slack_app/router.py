"""Platform admin endpoints for the Tracecat Slack app."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from tracecat.admin.slack_app.schemas import (
    SlackPlatformAppRead,
    SlackPlatformAppUpdate,
)
from tracecat.admin.slack_app.service import SlackPlatformAppService
from tracecat.agent.channel_bots.platform_app import clear_slack_platform_app_cache
from tracecat.agent.channel_bots.provider import get_channel_bot_admin
from tracecat.agent.channel_bots.schemas import (
    ChannelBotPlatformAppSetup,
    ChannelBotProvider,
)
from tracecat.auth.credentials import SuperuserRole, SuperuserStepUpRole
from tracecat.db.dependencies import AsyncDBSessionBypass
from tracecat.exceptions import TracecatValidationError

router = APIRouter(prefix="/slack-app", tags=["admin:slack-app"])


def _slack_setup() -> ChannelBotPlatformAppSetup:
    if (admin := get_channel_bot_admin()) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Channel bots require Tracecat Enterprise",
        )
    return admin.platform_app_setup(ChannelBotProvider.SLACK)


# Declared after the role in each route, so only a superuser learns the deployment's setup.
SlackSetup = Annotated[ChannelBotPlatformAppSetup, Depends(_slack_setup)]


@router.get("", response_model=SlackPlatformAppRead)
async def get_slack_app(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
    setup: SlackSetup,
) -> SlackPlatformAppRead:
    """The registration without its secrets, and what to paste into Slack."""
    return await SlackPlatformAppService(session, role).get(setup)


@router.put("", response_model=SlackPlatformAppRead)
async def save_slack_app(
    role: SuperuserStepUpRole,
    session: AsyncDBSessionBypass,
    setup: SlackSetup,
    params: SlackPlatformAppUpdate,
) -> SlackPlatformAppRead:
    """Register or change the app every organization installs."""
    try:
        result = await SlackPlatformAppService(session, role).save(params, setup)
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    clear_slack_platform_app_cache()
    return result


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_slack_app(
    role: SuperuserStepUpRole,
    session: AsyncDBSessionBypass,
) -> None:
    """Unregister the app; its installs stop serving until it is registered again."""
    await SlackPlatformAppService(session, role).remove()
    clear_slack_platform_app_cache()
