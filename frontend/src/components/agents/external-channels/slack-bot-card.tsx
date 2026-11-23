"use client"

import { ExternalLink, TriangleAlert } from "lucide-react"
import { Fragment, useState } from "react"
import { flushSync } from "react-dom"
import type {
  AgentChannelBotRead,
  AgentPresetRead,
  ChannelBotAppManifest,
  SlackBotCredentialsUpdate,
  SlackBotInstall,
} from "@/client"
import { ChannelAccessSelect } from "@/components/agents/external-channels/channel-access-select"
import {
  ASK_AN_AGENT_EDITOR,
  ChannelBotCard,
  ChannelBotDialog,
  ChannelBotLifecycleActions,
  useChannelBotDialog,
} from "@/components/agents/external-channels/channel-bot-card"
import {
  AppManifestButton,
  CredentialField,
  StatusLabel,
} from "@/components/agents/external-channels/channel-bot-fields"
import {
  byChannelName,
  CHANNEL_BOT_STATES,
  type ChannelBotDetailsOf,
  type ChannelBotState,
  channelBotDetails,
  channelBotReturnUrl,
  channelBotState,
  channelLabel,
  channelSummary,
  goToChannelBotAuthorization,
  groupSlackScopes,
  httpsUrlOrNull,
} from "@/components/agents/external-channels/channel-bot-utils"
import { slackAppSettingsUrl } from "@/components/agents/external-channels/slack-manifest"
import {
  CreateInSlackButton,
  CustomSlackAppDocsLink,
  SetupDoneLine,
  SlackAppTokenFields,
  SlackClientCredentialFields,
  SlackManualSetup,
} from "@/components/agents/external-channels/slack-setup-fields"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { Button } from "@/components/ui/button"
import { toast } from "@/components/ui/use-toast"
import {
  useChannelBotAppManifest,
  useChannelBotChannels,
  useChannelBotsBusy,
  useInstallAgentChannelBot,
  useUpdateAgentChannelBot,
} from "@/hooks/use-agent-channel-bots"
import { getApiErrorDetail } from "@/lib/errors"

const SLACK_APPS_URL = "https://api.slack.com/apps"
const CHANNEL_SUMMARY_CAP = 2
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
  const canUpdateAgent = useScopeCheck("agent:update") === true
  const disconnected = state === "needs_reconnect"
  // Unlike the platform card, a disconnected custom app can be stale too: reconnecting grants only what the app declares.
  const outdated =
    CHANNEL_BOT_STATES[state].installed &&
    (details?.update_needed === true || missingScopes.length > 0)
  const [reconnectOnOpen, setReconnectOnOpen] = useState(false)
  const { channels, hasMoreChannels } = useChannelBotChannels(
    workspaceId,
    bot?.id ?? "",
    {
      enabled: state === "active" && bot !== null,
    }
  )
  const channelLabels = (state === "active" ? [...(channels ?? [])] : [])
    .sort(byChannelName)
    .map((channel) => channelLabel("slack", channel))
  const summary = channelSummary(
    channelLabels,
    CHANNEL_SUMMARY_CAP,
    hasMoreChannels
  )

  return (
    <ChannelBotCard
      providerId="slack"
      title={TITLE}
      description={
        <>
          The bot gets its own name and icon, and answers only for this agent.{" "}
          <CustomSlackAppDocsLink />
        </>
      }
      state={state}
      status={
        disconnected || outdated ? (
          <StatusLabel icon={TriangleAlert} warning>
            {disconnected ? "Disconnected" : "Update available"}
          </StatusLabel>
        ) : undefined
      }
      onManage={dialog.openDialog}
      opening={dialog.opening}
    >
      {state === "active" ? (
        // The bot's Slack name is chosen at setup and not stored, so name no handle here.
        <p className="text-xs text-muted-foreground">
          Run <code>/invite</code> with the bot&apos;s name in any channel, then
          mention the bot there.
        </p>
      ) : null}
      {channelLabels.length > 0 ? (
        <p className="truncate text-xs text-muted-foreground">
          In {summary.shown.join(", ")}
          {summary.overflow}
        </p>
      ) : null}
      {bot && (disconnected || outdated) ? (
        <CustomAppNotice
          bot={bot}
          appName={preset.name}
          disconnected={disconnected}
          outdated={outdated}
          missingScopes={missingScopes}
          canUpdate={canUpdateAgent}
          opening={dialog.opening}
          onReconnect={() => {
            setReconnectOnOpen(true)
            dialog.openDialog()
          }}
        />
      ) : null}
      {dialog.open && bot && details ? (
        <SlackBotDialog
          key={bot.id}
          workspaceId={workspaceId}
          preset={preset}
          bot={bot}
          details={details}
          state={state}
          reconnect={disconnected || reconnectOnOpen}
          onClose={() => {
            setReconnectOnOpen(false)
            dialog.close()
          }}
        />
      ) : null}
    </ChannelBotCard>
  )
}

/** One notice for a stale or disconnected app, ending in Reconnect. */
function CustomAppNotice({
  bot,
  appName,
  disconnected,
  outdated,
  missingScopes,
  canUpdate,
  opening,
  onReconnect,
}: {
  bot: AgentChannelBotRead
  appName: string
  disconnected: boolean
  outdated: boolean
  missingScopes: string[]
  canUpdate: boolean
  opening: boolean
  onReconnect: () => void
}) {
  let title = "Disconnected from Slack."
  let body = "Reconnect to restore the bot."
  if (outdated) {
    title = disconnected
      ? "Slack app is out of date and disconnected."
      : "Slack app is out of date."
    body = disconnected
      ? "Paste the new manifest into the app in Slack, then reconnect. Until then the bot doesn't answer."
      : "The bot still answers. Paste the new manifest into the app in Slack, then reconnect to turn on new tools."
  }
  if (!canUpdate) {
    body = outdated
      ? "Ask someone who can edit this agent to update it."
      : ASK_AN_AGENT_EDITOR
  }
  return (
    <div role="status" className="space-y-2.5 text-xs">
      <div className="flex items-start gap-2">
        <TriangleAlert
          className="mt-px size-4 shrink-0 text-amber-600 dark:text-amber-400"
          aria-hidden
        />
        <p className="text-muted-foreground">
          <span className="font-medium text-foreground">{title}</span> {body}
        </p>
      </div>
      <div className="ml-6 space-y-2.5">
        {canUpdate ? (
          <div className="flex flex-wrap items-center gap-2">
            {outdated ? (
              <OutdatedAppActions bot={bot} appName={appName} />
            ) : null}
            <Button
              type="button"
              size="sm"
              onClick={onReconnect}
              disabled={opening}
            >
              Reconnect
            </Button>
          </div>
        ) : null}
        {outdated && missingScopes.length > 0 ? (
          <details>
            <summary className="cursor-pointer text-muted-foreground">
              What the agent gains ({missingScopes.length}{" "}
              {missingScopes.length === 1 ? "permission" : "permissions"})
            </summary>
            <dl className="mt-2 grid gap-x-4 gap-y-1 sm:grid-cols-[max-content_1fr]">
              {groupSlackScopes(missingScopes).map((group) => (
                <Fragment key={group.label}>
                  <dt className="font-medium">{group.label}</dt>
                  <dd className="text-muted-foreground">
                    {group.scopes.join(", ")}
                  </dd>
                </Fragment>
              ))}
            </dl>
          </details>
        ) : null}
      </div>
    </div>
  )
}

/** Copy the current manifest and open the app, to update it in Slack. */
function OutdatedAppActions({
  bot,
  appName,
}: {
  bot: AgentChannelBotRead
  appName: string
}) {
  const { appManifest, appManifestError } = useChannelBotAppManifest(
    bot,
    appName.trim() || TITLE
  )
  return (
    <>
      <AppManifestButton manifest={appManifest} />
      <Button type="button" size="sm" variant="outline" asChild>
        <a
          href={slackAppSettingsUrl(bot.provider_app_id)}
          target="_blank"
          rel="noreferrer"
        >
          Open app in Slack
          <ExternalLink className="ml-2 size-4" />
        </a>
      </Button>
      {appManifestError ? (
        <p className="basis-full text-destructive">
          {getApiErrorDetail(appManifestError) ??
            "Could not load the Slack app manifest."}
        </p>
      ) : null}
    </>
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
  reconnect,
  onClose,
}: {
  workspaceId: string
  preset: AgentPresetRead
  bot: AgentChannelBotRead
  details: ChannelBotDetailsOf<"slack">
  state: ChannelBotState
  /** Opens straight to reconnecting. */
  reconnect: boolean
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

  const [reconnecting, setReconnecting] = useState(reconnect)
  const [botName, setBotName] = useState(preset.name)
  const [appCreated, setAppCreated] = useState(false)
  const [clientId, setClientId] = useState(details.client_id ?? "")
  const [clientSecret, setClientSecret] = useState("")
  const [signingSecret, setSigningSecret] = useState("")
  const [appToken, setAppToken] = useState("")
  const [appRefreshToken, setAppRefreshToken] = useState("")

  const status = CHANNEL_BOT_STATES[state]
  const { appManifest, appManifestError } = useChannelBotAppManifest(
    bot,
    botName.trim() || TITLE
  )
  const manifestError = appManifestError
    ? (getApiErrorDetail(appManifestError) ??
      "Could not load the Slack app manifest.")
    : null
  const mode = slackDialogMode(status.installed, reconnecting)
  const socket = details.transport === "socket"
  // Rotating app-level tokens come with a refresh token that Slack shows once.
  const rotatingAppToken = appToken.trim().startsWith("xoxe.")
  // A first install creates the app before it asks for credentials.
  const showCredentials = mode !== "install" || appCreated

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
      if (rotatingAppToken) {
        body.app_refresh_token = appRefreshToken.trim()
      }
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
      (!socket ||
        (appToken.trim() && (!rotatingAppToken || appRefreshToken.trim())))
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
      description={
        mode === "install"
          ? "The bot gets its own name and icon, and answers only for this agent. Needs permission to create apps in your Slack workspace."
          : "The bot gets its own name and icon, and answers only for this agent."
      }
      details={
        <>
          {mode === "install" ? (
            <SlackAppCreateStep
              bot={bot}
              details={details}
              manifest={appManifest}
              manifestError={manifestError}
              botName={botName}
              onBotNameChange={setBotName}
              created={appCreated}
              onCreated={() => setAppCreated(true)}
              disabled={busy}
            />
          ) : (
            <SlackBotManualSetup
              bot={bot}
              details={details}
              manifest={appManifest}
              manifestError={manifestError}
              summary="Slack settings for this app"
            />
          )}
          {status.installed ? (
            <>
              <ChannelAccessSelect workspaceId={workspaceId} bot={bot} />
              <ChannelBotLifecycleActions
                workspaceId={workspaceId}
                bot={bot}
                state={state}
                onDisconnected={onClose}
              />
            </>
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
      {showCredentials ? (
        <>
          <div className="flex flex-wrap items-start gap-3">
            <p className="min-w-48 flex-1 text-xs text-muted-foreground">
              Paste the app&apos;s credentials. In the Slack app, open Basic
              Information → App Credentials.
            </p>
            <Button type="button" size="sm" variant="outline" asChild>
              <a href={SLACK_APPS_URL} target="_blank" rel="noreferrer">
                Open Slack apps
                <ExternalLink className="ml-2 size-4" />
              </a>
            </Button>
          </div>
          <SlackClientCredentialFields
            idPrefix="slack-bot"
            clientId={clientId}
            onClientIdChange={setClientId}
            clientSecret={clientSecret}
            onClientSecretChange={setClientSecret}
            signingSecret={signingSecret}
            onSigningSecretChange={setSigningSecret}
            clientSecretSaved={mode === "edit" && details.has_client_secret}
            signingSecretSaved={mode === "edit" && details.has_signing_secret}
            disabled={busy}
          />
          {socket && mode !== "edit" ? (
            <SlackAppTokenFields
              idPrefix="slack-bot"
              appToken={appToken}
              onAppTokenChange={setAppToken}
              refreshToken={appRefreshToken}
              onRefreshTokenChange={setAppRefreshToken}
              disabled={busy}
              refreshTokenHelp="Tracecat rotates both, so generate a new token for each connect."
            />
          ) : null}
          {socket && mode === "edit" ? (
            <p className="text-xs text-muted-foreground">
              Reconnecting replaces the app-level token.
            </p>
          ) : null}
        </>
      ) : null}
    </ChannelBotDialog>
  )
}

/** Bot name, then Slack's create-app page with the manifest filled in. */
function SlackAppCreateStep({
  bot,
  details,
  manifest,
  manifestError,
  botName,
  onBotNameChange,
  created,
  onCreated,
  disabled,
}: {
  bot: AgentChannelBotRead
  details: ChannelBotDetailsOf<"slack">
  manifest: ChannelBotAppManifest | null
  manifestError: string | null
  botName: string
  onBotNameChange: (value: string) => void
  created: boolean
  onCreated: () => void
  disabled: boolean
}) {
  if (created) {
    return (
      <div className="space-y-4">
        <SetupDoneLine>Slack app ready</SetupDoneLine>
        <SlackBotManualSetup
          bot={bot}
          details={details}
          manifest={manifest}
          manifestError={manifestError}
          summary="Create the app by hand instead"
        />
      </div>
    )
  }
  return (
    <div className="space-y-4">
      <CredentialField
        id="slack-bot-name"
        label="Bot name"
        value={botName}
        onChange={onBotNameChange}
        disabled={disabled}
        placeholder="Name the bot"
        help="Shown on every reply in Slack."
      />
      <div className="flex flex-wrap items-center gap-3">
        <CreateInSlackButton
          createUrl={manifest?.create_url ?? null}
          disabled={disabled || !botName.trim()}
          onOpen={onCreated}
        />
        <Button
          type="button"
          size="sm"
          variant="link"
          className="h-auto px-0 text-xs text-muted-foreground"
          onClick={onCreated}
          disabled={disabled}
        >
          I already have the app
        </Button>
      </div>
      <SlackBotManualSetup
        bot={bot}
        details={details}
        manifest={manifest}
        manifestError={manifestError}
        summary="Create the app by hand instead"
      />
    </div>
  )
}

/** The request and redirect URLs and the manifest, collapsed. */
function SlackBotManualSetup({
  bot,
  details,
  manifest,
  manifestError,
  summary,
}: {
  bot: AgentChannelBotRead
  details: ChannelBotDetailsOf<"slack">
  manifest: ChannelBotAppManifest | null
  manifestError: string | null
  summary: string
}) {
  const socket = details.transport === "socket"
  return (
    <>
      {manifestError ? (
        <p className="text-xs text-destructive">{manifestError}</p>
      ) : null}
      <SlackManualSetup
        summary={summary}
        requestUrl={socket ? null : bot.endpoint_url}
        oauthRedirectUrl={details.oauth_redirect_url}
        manifest={manifest}
      />
    </>
  )
}
