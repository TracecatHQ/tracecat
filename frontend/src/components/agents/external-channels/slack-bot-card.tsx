"use client"

import { ExternalLink } from "lucide-react"
import { useState } from "react"
import { flushSync } from "react-dom"
import type {
  AgentChannelBotRead,
  AgentPresetRead,
  SlackBotCredentialsUpdate,
  SlackBotInstall,
} from "@/client"
import {
  ChannelBotCard,
  ChannelBotDialog,
  ChannelBotLifecycleActions,
  useChannelBotDialog,
} from "@/components/agents/external-channels/channel-bot-card"
import {
  CopyField,
  CredentialField,
  EndpointField,
} from "@/components/agents/external-channels/channel-bot-fields"
import {
  CHANNEL_BOT_STATES,
  type ChannelBotDetailsOf,
  type ChannelBotState,
  channelBotDetails,
  channelBotReturnUrl,
  channelBotState,
  goToChannelBotAuthorization,
  httpsUrlOrNull,
} from "@/components/agents/external-channels/channel-bot-utils"
import { buildSlackManifest } from "@/components/agents/external-channels/slack-manifest"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { toast } from "@/components/ui/use-toast"
import {
  useChannelBotsBusy,
  useInstallAgentChannelBot,
  useUpdateAgentChannelBot,
} from "@/hooks/use-agent-channel-bots"
import { copyToClipboard } from "@/lib/utils"

const SLACK_APPS_URL = "https://api.slack.com/apps"
const TITLE = "Custom Slack app"
const RECONNECT_WARNING =
  "Reconnecting takes this bot offline until you approve the app in Slack."

/** The Slack app card on the channel bot API. */
export function SlackBotCard({
  workspaceId,
  preset,
  bot: listedBot,
}: {
  workspaceId: string
  preset: AgentPresetRead
  /** The preset's Slack bot, or null before one is reserved. */
  bot: AgentChannelBotRead | null
}) {
  const dialog = useChannelBotDialog({
    workspaceId,
    presetId: preset.id,
    provider: "slack",
    bot: listedBot,
  })
  const bot = dialog.bot
  const details = channelBotDetails(bot, "slack")
  const state = channelBotState(bot, details?.has_bot_token === true)
  const missingScopes = details?.missing_scopes ?? []
  const updateNeeded =
    CHANNEL_BOT_STATES[state].installed &&
    (details?.update_needed === true || missingScopes.length > 0)

  return (
    <ChannelBotCard
      providerId="slack"
      title={TITLE}
      description={
        <>
          Handle <code>app_mention</code> events and respond in-thread.
        </>
      }
      state={state}
      reconnectHint="Connect to Slack again to restore this bot."
      onManage={dialog.openDialog}
      opening={dialog.opening}
    >
      {updateNeeded ? (
        <Alert>
          <AlertTitle>Update needed</AlertTitle>
          <AlertDescription>
            Update the Slack app from the latest manifest, then connect again
            {missingScopes.length > 0
              ? ` to grant: ${missingScopes.join(", ")}`
              : ""}
            .
          </AlertDescription>
        </Alert>
      ) : null}
      {dialog.open && bot && details ? (
        <SlackBotDialog
          key={bot.id}
          workspaceId={workspaceId}
          preset={preset}
          bot={bot}
          details={details}
          state={state}
          onClose={dialog.close}
        />
      ) : null}
    </ChannelBotCard>
  )
}

type SlackDialogMode = "install" | "reconnect" | "edit"

function slackDialogMode(
  installed: boolean,
  reconnecting: boolean
): SlackDialogMode {
  if (!installed) {
    return "install"
  }
  return reconnecting ? "reconnect" : "edit"
}

/** The Slack bot's form; unmounting on close drops anything typed. */
function SlackBotDialog({
  workspaceId,
  preset,
  bot,
  details,
  state,
  onClose,
}: {
  workspaceId: string
  preset: AgentPresetRead
  bot: AgentChannelBotRead
  details: ChannelBotDetailsOf<"slack">
  state: ChannelBotState
  onClose: () => void
}) {
  const { installBot, installBotIsPending } = useInstallAgentChannelBot(
    workspaceId,
    "slack"
  )
  const { updateBot, updateBotIsPending } = useUpdateAgentChannelBot(
    workspaceId,
    "slack"
  )
  const busy = useChannelBotsBusy(workspaceId)

  const [reconnecting, setReconnecting] = useState(state === "needs_reconnect")
  const [clientId, setClientId] = useState(details.client_id ?? "")
  const [clientSecret, setClientSecret] = useState("")
  const [signingSecret, setSigningSecret] = useState("")
  const [appToken, setAppToken] = useState("")

  const status = CHANNEL_BOT_STATES[state]
  const mode = slackDialogMode(status.installed, reconnecting)
  const socket = details.transport === "socket"

  function handleInstall(): void {
    const body: SlackBotInstall = {
      provider: "slack",
      bot_id: bot.id,
      agent_preset_id: preset.id,
      client_id: clientId.trim(),
      client_secret: clientSecret.trim(),
      signing_secret: signingSecret.trim(),
      return_url: channelBotReturnUrl(),
    }
    if (socket) {
      body.app_token = appToken.trim()
    }
    installBot(body, {
      onSuccess: (result) => {
        const url = httpsUrlOrNull(result.authorization_url)
        if (!url) {
          toast({
            title: "Could not connect the Slack bot",
            description: "Slack did not return a valid authorization URL.",
            variant: "destructive",
          })
          return
        }
        // Unmount the form first so a back-forward cache restore has no secrets.
        flushSync(onClose)
        goToChannelBotAuthorization(url)
      },
    })
  }

  function handleSaveCredentials(): void {
    const credentials: SlackBotCredentialsUpdate = { provider: "slack" }
    const nextClientId = clientId.trim()
    if (nextClientId && nextClientId !== details.client_id) {
      credentials.client_id = nextClientId
    }
    if (clientSecret.trim()) {
      credentials.client_secret = clientSecret.trim()
    }
    if (signingSecret.trim()) {
      credentials.signing_secret = signingSecret.trim()
    }
    updateBot(
      { botId: bot.id, requestBody: { credentials } },
      {
        onSuccess: () => {
          onClose()
          toast({ title: "Slack credentials saved" })
        },
      }
    )
  }

  const canInstall = Boolean(
    clientId.trim() &&
      clientSecret.trim() &&
      signingSecret.trim() &&
      (!socket || appToken.trim())
  )
  const canSaveCredentials = Boolean(
    (clientId.trim() && clientId.trim() !== details.client_id) ||
      clientSecret.trim() ||
      signingSecret.trim()
  )
  const submit: Record<
    SlackDialogMode,
    { label: string; run: () => void; ready: boolean }
  > = {
    install: {
      label: "Connect to Slack",
      run: handleInstall,
      ready: canInstall,
    },
    reconnect: {
      label: "Reconnect to Slack",
      run: handleInstall,
      ready: canInstall,
    },
    edit: {
      label: "Save credentials",
      run: handleSaveCredentials,
      ready: canSaveCredentials,
    },
  }
  const takesOffline = mode === "reconnect" && state === "active"

  return (
    <ChannelBotDialog
      onClose={onClose}
      busy={busy}
      title={TITLE}
      description="Connect a Slack app you own to this agent."
      details={
        <>
          <SlackBotDetailsSection
            bot={bot}
            details={details}
            appName={preset.name}
          />
          {status.installed ? (
            <ChannelBotLifecycleActions
              workspaceId={workspaceId}
              bot={bot}
              state={state}
              onDisconnected={onClose}
            />
          ) : null}
        </>
      }
      onSubmit={submit[mode].run}
      submitLabel={submit[mode].label}
      canSubmit={!busy && submit[mode].ready}
      submitting={installBotIsPending || updateBotIsPending}
      submitNote={takesOffline ? RECONNECT_WARNING : null}
      secondaryAction={
        mode === "edit" ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => setReconnecting(true)}
            disabled={busy}
          >
            Reconnect
          </Button>
        ) : null
      }
    >
      <CredentialField
        id="slack-cid"
        label="Client ID"
        value={clientId}
        onChange={setClientId}
        disabled={busy}
        placeholder="1234567890.1234567890"
      />
      <CredentialField
        id="slack-cs"
        label="Client secret"
        secret
        value={clientSecret}
        onChange={setClientSecret}
        disabled={busy}
        placeholder={
          mode === "edit" && details.has_client_secret
            ? "Stored; enter a new one to replace it"
            : "Slack app client secret"
        }
      />
      <CredentialField
        id="slack-ss"
        label="Signing secret"
        secret
        value={signingSecret}
        onChange={setSigningSecret}
        disabled={busy}
        placeholder={
          mode === "edit" && details.has_signing_secret
            ? "Stored; enter a new one to replace it"
            : "Slack signing secret"
        }
      />
      {socket && mode !== "edit" ? (
        <CredentialField
          id="slack-at"
          label="App-level token"
          secret
          value={appToken}
          onChange={setAppToken}
          disabled={busy}
          placeholder="xapp-..."
          help="Generate one with the connections:write scope under Basic Information in your Slack app."
        />
      ) : null}
      {socket && mode === "edit" ? (
        <p className="text-xs text-muted-foreground">
          Reconnecting replaces the app-level token.
        </p>
      ) : null}
    </ChannelBotDialog>
  )
}

/** Where the Slack app sends events, its redirect URL and its manifest. */
function SlackBotDetailsSection({
  bot,
  details,
  appName,
}: {
  bot: AgentChannelBotRead
  details: ChannelBotDetailsOf<"slack">
  appName: string
}) {
  const socket = details.transport === "socket"
  const endpointUrl = socket ? null : bot.endpoint_url

  function handleCopyManifest(): void {
    const manifest = buildSlackManifest({
      endpointUrl,
      oauthRedirectUrl: details.oauth_redirect_url,
      appName,
    })
    void copyToClipboard({
      value: JSON.stringify(manifest, null, 2),
      message: "Slack manifest copied",
    })
  }

  return (
    <>
      {endpointUrl ? (
        <EndpointField
          label="Endpoint URL"
          value={endpointUrl}
          help="Paste into Event Subscriptions and Interactivity in your Slack app."
          provider="slack"
        />
      ) : null}
      {socket ? (
        <p className="text-xs text-muted-foreground">
          This deployment receives Slack events over Socket Mode, so the app
          needs no request URL.
        </p>
      ) : null}
      <CopyField
        label="OAuth redirect URL"
        value={details.oauth_redirect_url}
        help="Add under OAuth & Permissions in your Slack app."
      />
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={handleCopyManifest}
          disabled={!socket && !endpointUrl}
        >
          Copy app manifest
        </Button>
        <Button type="button" size="sm" variant="outline" asChild>
          <a href={SLACK_APPS_URL} target="_blank" rel="noreferrer">
            <ExternalLink className="mr-2 size-4" />
            Open Slack apps
          </a>
        </Button>
      </div>
    </>
  )
}
