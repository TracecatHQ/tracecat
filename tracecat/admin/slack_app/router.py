"""Platform admin endpoints for the Tracecat Slack app."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from tracecat.admin.slack_app.schemas import (
    SlackPlatformAppRead,
    SlackPlatformAppSetup,
    SlackPlatformAppSetupStepsUpdate,
    SlackPlatformAppUpdate,
)
from tracecat.admin.slack_app.service import (
    SlackPlatformAppNotRegistered,
    SlackPlatformAppService,
    SlackPlatformAppStillInstalled,
)
from tracecat.agent.channel_bots.platform_app import clear_slack_platform_app_cache
from tracecat.agent.channel_bots.provider import get_channel_bot_admin
from tracecat.agent.channel_bots.schemas import ChannelBotProvider
from tracecat.auth.credentials import SuperuserRole, SuperuserStepUpRole
from tracecat.db.dependencies import AsyncDBSessionBypass
from tracecat.exceptions import TracecatValidationError

router = APIRouter(prefix="/slack-app", tags=["admin:slack-app"])


def _slack_setup() -> SlackPlatformAppSetup:
    if (admin := get_channel_bot_admin()) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Channel bots require Tracecat Enterprise",
        )
    setup = admin.platform_app_setup(ChannelBotProvider.SLACK)
    manifest = admin.platform_app_manifest(ChannelBotProvider.SLACK)
    return SlackPlatformAppSetup(**setup.model_dump(), manifest=manifest)


# Declared after the role in each route, so only a superuser learns the deployment's setup.
SlackSetup = Annotated[SlackPlatformAppSetup, Depends(_slack_setup)]


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
    except SlackPlatformAppStillInstalled as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    clear_slack_platform_app_cache()
    return result


# No security key step-up: these are an admin's notes, not credentials.
@router.patch("/setup", response_model=SlackPlatformAppRead)
async def mark_slack_app_setup_steps(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
    setup: SlackSetup,
    params: SlackPlatformAppSetupStepsUpdate,
) -> SlackPlatformAppRead:
    """Remember which Slack-only setup steps an admin marked done."""
    try:
        result = await SlackPlatformAppService(session, role).mark_setup_steps(
            params, setup
        )
    except SlackPlatformAppNotRegistered as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    clear_slack_platform_app_cache()
    return result


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_slack_app(
    role: SuperuserStepUpRole,
    session: AsyncDBSessionBypass,
) -> None:
    """Unregister the app; refused while any organization still has it installed."""
    try:
        await SlackPlatformAppService(session, role).remove()
    except SlackPlatformAppStillInstalled as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    clear_slack_platform_app_cache()
