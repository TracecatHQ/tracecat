"use client"

import {
  ArrowLeft,
  ArrowLeftRight,
  CheckCircle2,
  ExternalLink,
  Loader2,
  Plus,
  RefreshCw,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import type { AgentPresetRead } from "@/client"
import { CopyButton } from "@/components/copy-button"
import { ProviderIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { toast } from "@/components/ui/use-toast"
import {
  useAgentChannelTokens,
  useCreateAgentChannelToken,
  useDeleteAgentChannelToken,
  useRotateAgentChannelToken,
  useStartSlackOAuth,
} from "@/hooks"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { copyToClipboard } from "@/lib/utils"

type SetupMethod = "existing" | "new"
type DialogStage = "choose" | "guide" | "connect"
const PROVISIONAL_BOT_TOKEN = "__tracecat_pending_bot_token__"
const PROVISIONAL_SIGNING_SECRET_PREFIX = "__tracecat_pending_signing_secret__"
type SlackChannelConfig = {
  slack_bot_token: string
  slack_signing_secret: string
  slack_client_id?: string | null
  slack_client_secret?: string | null
}

export function buildSlackManifest({
  endpointUrl,
  oauthRedirectUrl,
  appName,
  includeEventSubscriptions,
}: {
  endpointUrl: string
  oauthRedirectUrl: string
  appName: string
  includeEventSubscriptions: boolean
}): Record<string, unknown> {
  const settings: Record<string, unknown> = {
    interactivity: includeEventSubscriptions
      ? { is_enabled: true, request_url: endpointUrl }
      : { is_enabled: false },
    org_deploy_enabled: false,
    socket_mode_enabled: false,
    token_rotation_enabled: false,
  }
  if (includeEventSubscriptions) {
    settings.event_subscriptions = {
      request_url: endpointUrl,
      bot_events: ["app_mention"],
    }
  }

  return {
    display_information: {
      name: appName,
      description: "Tracecat external channel agent",
      background_color: "#111827",
    },
    features: {
      app_home: {
        home_tab_enabled: false,
        messages_tab_enabled: true,
        messages_tab_read_only_enabled: true,
      },
      bot_user: {
        display_name: appName,
        always_online: true,
      },
    },
    oauth_config: {
      scopes: {
        bot: [
          "app_mentions:read",
          "channels:history",
          "chat:write",
          "chat:write.customize",
          "groups:history",
          "im:history",
          "mpim:history",
          "reactions:read",
          "reactions:write",
        ],
      },
      redirect_urls: [oauthRedirectUrl],
    },
    settings,
  }
}

function buildSlackOAuthRedirectUrlFromEndpoint(endpointUrl: string): string {
  const marker = "/agent/channels/slack/"
  const markerIndex = endpointUrl.indexOf(marker)
  if (markerIndex === -1) {
    return endpointUrl
  }
  const base = endpointUrl.slice(0, markerIndex)
  return `${base}${marker}oauth/callback`
}

function SetupListItem({
  index,
  title,
  description,
  action,
}: {
  index: number
  title: string
  description: string
  action?: React.ReactNode
}) {
  return (
    <div className="flex items-start gap-3 rounded border px-3 py-3">
      <div className="flex size-6 shrink-0 items-center justify-center rounded-full border text-xs font-medium">
        {index}
      </div>
      <div className="min-w-0 flex-1 space-y-1">
        <p className="text-sm font-medium">{title}</p>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>
      {action}
    </div>
  )
}

export function SlackChannelPanel({
  workspaceId,
  preset,
}: {
  workspaceId: string
  preset: AgentPresetRead | null
}) {
  const {
    isFeatureEnabled: isFeatureEnabledFlag,
    isLoading: isLoadingFeatures,
  } = useFeatureFlag()
  const channelsEnabled = isFeatureEnabledFlag("agent-channels")

  if (isLoadingFeatures) {
    return <CenteredSpinner />
  }

  if (!channelsEnabled) {
    return (
      <div className="size-full p-6">
        <Alert>
          <AlertTitle>Feature not enabled</AlertTitle>
          <AlertDescription>
            External channels are unavailable for this workspace.
          </AlertDescription>
        </Alert>
      </div>
    )
  }

  const { tokens, tokensIsLoading, tokensError } = useAgentChannelTokens(
    workspaceId,
    {
      enabled: Boolean(preset?.id),
      agentPresetId: preset?.id,
      channelType: "slack",
    }
  )

  const { createChannelToken, createChannelTokenIsPending } =
    useCreateAgentChannelToken(workspaceId)
  const { rotateChannelToken, rotateChannelTokenIsPending } =
    useRotateAgentChannelToken(workspaceId)
  const { deleteChannelToken, deleteChannelTokenIsPending } =
    useDeleteAgentChannelToken(workspaceId)
  const { startSlackOAuth, startSlackOAuthIsPending } =
    useStartSlackOAuth(workspaceId)

  const [dialogOpen, setDialogOpen] = useState(false)
  const [dialogStage, setDialogStage] = useState<DialogStage>("choose")
  const [setupMethod, setSetupMethod] = useState<SetupMethod | null>(null)
  const [slackClientId, setSlackClientId] = useState("")
  const [slackClientSecret, setSlackClientSecret] = useState("")
  const [slackSigningSecret, setSlackSigningSecret] = useState("")
  const [provisionedTokenId, setProvisionedTokenId] = useState<string | null>(
    null
  )
  const [provisionedEndpointUrl, setProvisionedEndpointUrl] = useState("")

  const token = useMemo(() => {
    if (!tokens || tokens.length === 0) {
      return null
    }
    const activeToken = tokens.find((item) => item.is_active)
    return activeToken ?? tokens[0]
  }, [tokens])

  const tokenId = token?.id ?? provisionedTokenId
  const endpointUrl = token?.endpoint_url ?? provisionedEndpointUrl
  const tokenConfig = token?.config as SlackChannelConfig | undefined
  const oauthRedirectUrl = useMemo(() => {
    if (!endpointUrl) {
      return ""
    }
    return buildSlackOAuthRedirectUrlFromEndpoint(endpointUrl)
  }, [endpointUrl])
  const manifestText = useMemo(() => {
    if (!endpointUrl) {
      return ""
    }
    return JSON.stringify(
      buildSlackManifest({
        endpointUrl,
        oauthRedirectUrl,
        appName: preset?.name ?? "Tracecat",
        includeEventSubscriptions: true,
      }),
      null,
      2
    )
  }, [endpointUrl, oauthRedirectUrl, preset?.name, setupMethod])

  const isBusy =
    createChannelTokenIsPending ||
    rotateChannelTokenIsPending ||
    deleteChannelTokenIsPending ||
    startSlackOAuthIsPending

  useEffect(() => {
    if (!token) {
      setSlackClientId("")
      setSlackClientSecret("")
      setSlackSigningSecret("")
      return
    }
    setProvisionedTokenId(null)
    setProvisionedEndpointUrl("")
    setSlackClientId(tokenConfig?.slack_client_id ?? "")
    setSlackClientSecret(tokenConfig?.slack_client_secret ?? "")
    const signingSecretValue = tokenConfig?.slack_signing_secret.startsWith(
      PROVISIONAL_SIGNING_SECRET_PREFIX
    )
      ? ""
      : (tokenConfig?.slack_signing_secret ?? "")
    setSlackSigningSecret(signingSecretValue)
  }, [token, tokenConfig])

  useEffect(() => {
    if (typeof window === "undefined") {
      return
    }
    const url = new URL(window.location.href)
    const slackConnectStatus = url.searchParams.get("slack_connect")
    if (!slackConnectStatus) {
      return
    }
    const message = url.searchParams.get("slack_message")
    if (slackConnectStatus === "success") {
      toast({
        title: "Slack App connected",
        description: "Your channel token is now active.",
      })
    } else {
      toast({
        title: "Slack App connection failed",
        description: message || "Unable to complete Slack OAuth flow.",
        variant: "destructive",
      })
    }
    url.searchParams.delete("slack_connect")
    url.searchParams.delete("slack_message")
    window.history.replaceState({}, "", url.toString())
  }, [])

  if (!preset) {
    return (
      <div className="flex h-full items-center justify-center px-4 text-center text-sm text-muted-foreground">
        Save this agent preset before configuring external channels.
      </div>
    )
  }

  if (tokensIsLoading) {
    return <CenteredSpinner />
  }

  if (tokensError) {
    const detail =
      typeof tokensError.body?.detail === "string"
        ? tokensError.body.detail
        : tokensError.message
    return (
      <div className="p-4">
        <Alert variant="destructive">
          <AlertTitle>Unable to load channel settings</AlertTitle>
          <AlertDescription>{detail}</AlertDescription>
        </Alert>
      </div>
    )
  }

  async function handleConnectToSlack(): Promise<void> {
    const presetId = preset?.id
    if (!presetId) {
      return
    }
    const clientId = slackClientId.trim()
    const clientSecret = slackClientSecret.trim()
    const signingSecret = slackSigningSecret.trim()
    if (!clientId || !clientSecret || !signingSecret) {
      toast({
        title: "Missing fields",
        description:
          "Client ID, client secret, and signing secret are required.",
        variant: "destructive",
      })
      return
    }
    if (typeof window === "undefined") {
      return
    }
    try {
      const response = await startSlackOAuth({
        tokenId: tokenId ?? undefined,
        agentPresetId: presetId,
        clientId,
        clientSecret,
        signingSecret,
        returnUrl: window.location.href,
      })
      window.location.assign(response.authorization_url)
    } catch {
      return
    }
  }

  async function handleRotate(): Promise<void> {
    if (!token) {
      return
    }
    try {
      await rotateChannelToken({ tokenId: token.id })
    } catch {
      return
    }
  }

  async function handleDelete(): Promise<void> {
    if (!token) {
      return
    }
    try {
      await deleteChannelToken({ tokenId: token.id })
      setProvisionedTokenId(null)
      setProvisionedEndpointUrl("")
    } catch {
      return
    }
  }

  function handleCopyManifest(): void {
    if (!manifestText) {
      return
    }
    void copyToClipboard({
      value: manifestText,
      message: "Slack manifest copied",
    })
  }

  function resetDialogState(): void {
    setDialogStage("choose")
    setSetupMethod(null)
  }

  async function handleNextFromDialog(): Promise<void> {
    if (dialogStage === "guide") {
      setDialogStage("connect")
      return
    }

    await handleConnectToSlack()
  }

  async function handleSelectMethod(method: SetupMethod): Promise<void> {
    setSetupMethod(method)
    const presetId = preset?.id
    if (!presetId) {
      return
    }
    if (tokenId) {
      setDialogStage(method === "new" ? "guide" : "connect")
      return
    }

    try {
      const createdToken = await createChannelToken({
        agent_preset_id: presetId,
        channel_type: "slack",
        config: {
          slack_bot_token: PROVISIONAL_BOT_TOKEN,
          slack_signing_secret: `${PROVISIONAL_SIGNING_SECRET_PREFIX}${crypto.randomUUID()}`,
        },
        is_active: false,
      })
      setProvisionedTokenId(createdToken.id)
      setProvisionedEndpointUrl(createdToken.endpoint_url)
      setDialogStage(method === "new" ? "guide" : "connect")
    } catch {
      return
    }
  }

  return (
    <ScrollArea className="h-full">
      <div className="flex flex-col gap-4 px-6 py-6 pb-16">
        <div className="space-y-1">
          <h3 className="text-base font-medium">Integrations</h3>
          <p className="text-xs text-muted-foreground">
            Connect this agent preset to external channels.
          </p>
        </div>

        <div className="rounded border">
          <div className="flex items-center gap-4 p-4">
            <div className="flex size-10 items-center justify-center rounded border bg-muted/30">
              <ProviderIcon providerId="slack" className="size-7 p-0.5" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">Custom Slack App</p>
              <p className="text-xs text-muted-foreground">
                Handle `app_mention` events and respond in-thread.
              </p>
            </div>
            <Button type="button" size="sm" onClick={() => setDialogOpen(true)}>
              {token ? "Manage" : "Connect"}
            </Button>
          </div>
        </div>

        <Dialog
          open={dialogOpen}
          onOpenChange={(open) => {
            setDialogOpen(open)
            if (!open) {
              resetDialogState()
            }
          }}
        >
          <DialogContent className="max-w-xl">
            <DialogHeader>
              <DialogTitle
                className={
                  dialogStage === "choose"
                    ? "text-xl font-semibold tracking-tight leading-tight"
                    : ""
                }
              >
                {dialogStage === "choose"
                  ? "How would you like to connect your Slack app to this agent?"
                  : "Setup guide"}
              </DialogTitle>
              {dialogStage === "guide" ? (
                <DialogDescription>
                  Follow these steps to create a new Slack app.
                </DialogDescription>
              ) : null}
              {dialogStage === "connect" ? (
                <DialogDescription>
                  Enter your Slack app credentials to start OAuth install.
                </DialogDescription>
              ) : null}
            </DialogHeader>

            {dialogStage === "choose" ? (
              <div className="space-y-5">
                <button
                  type="button"
                  className="group flex w-full items-center gap-4 rounded-lg border px-4 py-4 text-left transition-colors hover:bg-muted/20"
                  disabled={isBusy}
                  onClick={() => void handleSelectMethod("new")}
                >
                  <div className="flex size-10 shrink-0 items-center justify-center rounded-md border bg-muted/20">
                    <Plus className="size-4 text-muted-foreground" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-base font-medium tracking-tight">
                      New App
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Create a new Slack App using a pre-filled manifest
                    </p>
                  </div>
                  <span className="text-xs font-medium text-muted-foreground transition-colors group-hover:text-foreground">
                    Choose
                  </span>
                </button>

                <button
                  type="button"
                  className="group flex w-full items-center gap-4 rounded-lg border px-4 py-4 text-left transition-colors hover:bg-muted/20"
                  disabled={isBusy}
                  onClick={() => void handleSelectMethod("existing")}
                >
                  <div className="flex size-10 shrink-0 items-center justify-center rounded-md border bg-muted/20">
                    <ArrowLeftRight className="size-4 text-muted-foreground" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-base font-medium tracking-tight">
                      Existing App
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Connect an existing Slack App to this agent
                    </p>
                  </div>
                  <span className="text-xs font-medium text-muted-foreground transition-colors group-hover:text-foreground">
                    Choose
                  </span>
                </button>
              </div>
            ) : null}

            {dialogStage === "guide" ? (
              <div className="space-y-4">
                <div className="space-y-2">
                  <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    In Tracecat
                  </p>
                  <SetupListItem
                    index={1}
                    title="Export app manifest"
                    description="Copy the manifest generated for this agent preset."
                    action={
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={handleCopyManifest}
                        disabled={!manifestText}
                      >
                        Copy
                      </Button>
                    }
                  />
                </div>

                <div className="space-y-2">
                  <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    In Slack
                  </p>
                  <SetupListItem
                    index={2}
                    title="Create app from manifest"
                    description="Open Slack Apps, choose Create New App, then select From an app manifest."
                    action={
                      <Button type="button" size="sm" variant="outline" asChild>
                        <a
                          href="https://api.slack.com/apps"
                          target="_blank"
                          rel="noreferrer"
                        >
                          <ExternalLink className="mr-2 size-4" />
                          Open
                        </a>
                      </Button>
                    }
                  />
                  <SetupListItem
                    index={3}
                    title="Install the app"
                    description="Install to your workspace. You will add Event Subscriptions after connecting app credentials in Tracecat."
                  />
                </div>

                <div className="space-y-2">
                  <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    Back in Tracecat
                  </p>
                  <SetupListItem
                    index={4}
                    title='Continue with "Next"'
                    description="Proceed to connect your Client ID, client secret, and signing secret."
                  />
                </div>
              </div>
            ) : null}

            {dialogStage === "connect" ? (
              <div className="space-y-4">
                <div className="space-y-2">
                  <label className="text-xs font-medium">Endpoint URL</label>
                  <Input
                    readOnly
                    value={endpointUrl}
                    placeholder="Will appear after initial connect"
                  />
                  {endpointUrl ? (
                    <CopyButton
                      value={endpointUrl}
                      toastMessage="Endpoint URL copied"
                      tooltipMessage="Copy endpoint URL"
                    />
                  ) : null}
                </div>
                <div className="space-y-2">
                  <label className="text-xs font-medium">
                    OAuth redirect URL
                  </label>
                  <Input
                    readOnly
                    value={oauthRedirectUrl}
                    placeholder="Will appear after initial connect"
                  />
                  {oauthRedirectUrl ? (
                    <CopyButton
                      value={oauthRedirectUrl}
                      toastMessage="OAuth redirect URL copied"
                      tooltipMessage="Copy OAuth redirect URL"
                    />
                  ) : null}
                </div>
                <div className="space-y-2">
                  <label className="text-xs font-medium">Client ID</label>
                  <Input
                    value={slackClientId}
                    onChange={(event) => setSlackClientId(event.target.value)}
                    disabled={isBusy}
                    placeholder="1234567890.1234567890"
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-xs font-medium">Client secret</label>
                  <Input
                    type="password"
                    value={slackClientSecret}
                    onChange={(event) =>
                      setSlackClientSecret(event.target.value)
                    }
                    disabled={isBusy}
                    placeholder="Slack App client secret"
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-xs font-medium">Signing secret</label>
                  <Input
                    type="password"
                    value={slackSigningSecret}
                    onChange={(event) =>
                      setSlackSigningSecret(event.target.value)
                    }
                    disabled={isBusy}
                    placeholder="Slack signing secret"
                  />
                </div>
                {token ? (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => void handleRotate()}
                      disabled={isBusy}
                    >
                      <RefreshCw className="mr-2 size-4" />
                      Rotate endpoint
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => void handleDelete()}
                      disabled={isBusy}
                    >
                      Disconnect
                    </Button>
                  </div>
                ) : null}
              </div>
            ) : null}

            {dialogStage !== "choose" ? (
              <>
                <Separator />

                <div className="flex items-center justify-between">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => {
                      if (dialogStage === "guide") {
                        setDialogStage("choose")
                        return
                      }
                      if (dialogStage === "connect") {
                        setDialogStage(
                          setupMethod === "new" ? "guide" : "choose"
                        )
                      }
                    }}
                  >
                    <ArrowLeft className="mr-2 size-4" />
                    Back
                  </Button>
                  <Button
                    type="button"
                    onClick={() => void handleNextFromDialog()}
                  >
                    {isBusy ? (
                      <Loader2 className="mr-2 size-4 animate-spin" />
                    ) : null}
                    {dialogStage === "connect" ? "Connect to Slack" : "Next"}
                  </Button>
                </div>
              </>
            ) : null}
          </DialogContent>
        </Dialog>

        {token?.is_active ? (
          <div className="flex items-center gap-2 text-xs text-emerald-600">
            <CheckCircle2 className="size-4" />
            Slack integration connected.
          </div>
        ) : null}
      </div>
    </ScrollArea>
  )
}
