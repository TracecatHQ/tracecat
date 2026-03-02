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
import { Badge } from "@/components/ui/badge"
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
import { Switch } from "@/components/ui/switch"
import { toast } from "@/components/ui/use-toast"
import {
  useAgentChannelTokens,
  useCreateAgentChannelToken,
  useDeleteAgentChannelToken,
  useRotateAgentChannelToken,
  useUpdateAgentChannelToken,
} from "@/hooks"
import { copyToClipboard } from "@/lib/utils"

type SetupMethod = "existing" | "new"
type DialogStage = "choose" | "guide" | "connect"
const PROVISIONAL_BOT_TOKEN = "__tracecat_pending_bot_token__"
const PROVISIONAL_SIGNING_SECRET_PREFIX = "__tracecat_pending_signing_secret__"

export function buildSlackManifest({
  endpointUrl,
  appName,
}: {
  endpointUrl: string
  appName: string
}): Record<string, unknown> {
  return {
    display_information: {
      name: `${appName} agent`,
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
        display_name: `${appName} agent`,
        always_online: false,
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
    },
    settings: {
      event_subscriptions: {
        request_url: endpointUrl,
        bot_events: ["app_mention"],
      },
      interactivity: { is_enabled: false },
      org_deploy_enabled: false,
      socket_mode_enabled: false,
      token_rotation_enabled: false,
    },
  }
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
  const { updateChannelToken, updateChannelTokenIsPending } =
    useUpdateAgentChannelToken(workspaceId)
  const { rotateChannelToken, rotateChannelTokenIsPending } =
    useRotateAgentChannelToken(workspaceId)
  const { deleteChannelToken, deleteChannelTokenIsPending } =
    useDeleteAgentChannelToken(workspaceId)

  const [dialogOpen, setDialogOpen] = useState(false)
  const [dialogStage, setDialogStage] = useState<DialogStage>("choose")
  const [setupMethod, setSetupMethod] = useState<SetupMethod | null>(null)
  const [slackBotToken, setSlackBotToken] = useState("")
  const [slackSigningSecret, setSlackSigningSecret] = useState("")
  const [isActive, setIsActive] = useState(true)
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
  const manifestText = useMemo(() => {
    if (!endpointUrl) {
      return ""
    }
    return JSON.stringify(
      buildSlackManifest({
        endpointUrl,
        appName: preset?.name ?? "Tracecat",
      }),
      null,
      2
    )
  }, [endpointUrl, preset?.name])

  const isBusy =
    createChannelTokenIsPending ||
    updateChannelTokenIsPending ||
    rotateChannelTokenIsPending ||
    deleteChannelTokenIsPending

  useEffect(() => {
    if (!token) {
      setSlackBotToken("")
      setSlackSigningSecret("")
      setIsActive(true)
      return
    }
    setProvisionedTokenId(null)
    setProvisionedEndpointUrl("")
    const tokenValue =
      token.config.slack_bot_token === PROVISIONAL_BOT_TOKEN
        ? ""
        : token.config.slack_bot_token
    const signingSecretValue = token.config.slack_signing_secret.startsWith(
      PROVISIONAL_SIGNING_SECRET_PREFIX
    )
      ? ""
      : token.config.slack_signing_secret
    setSlackBotToken(tokenValue)
    setSlackSigningSecret(signingSecretValue)
    setIsActive(token.is_active)
  }, [token])

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

  async function handleSave(): Promise<void> {
    const presetId = preset?.id
    if (!presetId) {
      return
    }
    const botToken = slackBotToken.trim()
    const signingSecret = slackSigningSecret.trim()
    if (!botToken || !signingSecret) {
      toast({
        title: "Missing fields",
        description: "Bot token and signing secret are required.",
        variant: "destructive",
      })
      return
    }

    if (tokenId) {
      await updateChannelToken({
        tokenId,
        requestBody: {
          config: {
            slack_bot_token: botToken,
            slack_signing_secret: signingSecret,
          },
          is_active: isActive,
        },
      })
      return
    }

    await createChannelToken({
      agent_preset_id: presetId,
      channel_type: "slack",
      config: {
        slack_bot_token: botToken,
        slack_signing_secret: signingSecret,
      },
      is_active: isActive,
    })
  }

  async function handleRotate(): Promise<void> {
    if (!token) {
      return
    }
    await rotateChannelToken({ tokenId: token.id })
  }

  async function handleDelete(): Promise<void> {
    if (!token) {
      return
    }
    await deleteChannelToken({ tokenId: token.id })
    setProvisionedTokenId(null)
    setProvisionedEndpointUrl("")
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

    await handleSave()
  }

  const integrationStatus = token?.is_active ? "Connected" : "Not connected"

  async function handleSelectMethod(method: SetupMethod): Promise<void> {
    setSetupMethod(method)
    const presetId = preset?.id
    if (!presetId) {
      return
    }
    if (method === "existing") {
      setDialogStage("connect")
      return
    }

    if (tokenId) {
      setDialogStage("guide")
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
      setDialogStage("guide")
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
            <Badge variant={token?.is_active ? "default" : "secondary"}>
              {integrationStatus}
            </Badge>
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
                  Connect your Slack credentials.
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
                    title="Install and verify events"
                    description="Install the app to your workspace and confirm app mentions are enabled."
                  />
                </div>

                <div className="space-y-2">
                  <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    Back in Tracecat
                  </p>
                  <SetupListItem
                    index={4}
                    title='Continue with "Next"'
                    description="Proceed to connect the bot token and signing secret."
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
                  <label className="text-xs font-medium">Bot token</label>
                  <Input
                    type="password"
                    value={slackBotToken}
                    onChange={(event) => setSlackBotToken(event.target.value)}
                    disabled={isBusy}
                    placeholder="xoxb-..."
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
                <div className="flex items-center justify-between rounded border px-3 py-2">
                  <span className="text-xs font-medium">Active</span>
                  <Switch
                    checked={isActive}
                    onCheckedChange={setIsActive}
                    disabled={isBusy}
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
                    {dialogStage === "connect" ? "Connect" : "Next"}
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
