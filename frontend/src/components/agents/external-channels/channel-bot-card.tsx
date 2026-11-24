"use client"

import {
  CircleCheck,
  CircleDashed,
  CirclePause,
  Loader2,
  type LucideIcon,
  Pause,
  Play,
  TriangleAlert,
  Unplug,
} from "lucide-react"
import { type ReactNode, useId, useState } from "react"
import type { AgentChannelBotRead, ChannelBotProvider } from "@/client"
import {
  StatusDot,
  StatusLabel,
} from "@/components/agents/external-channels/channel-bot-fields"
import {
  CHANNEL_BOT_PROVIDER_NAMES,
  CHANNEL_BOT_STATES,
  type ChannelBotState,
} from "@/components/agents/external-channels/channel-bot-utils"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { ProviderIcon } from "@/components/icons"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Separator } from "@/components/ui/separator"
import { toast } from "@/components/ui/use-toast"
import {
  useChannelBotsBusy,
  useCreateAgentChannelBot,
  useDisconnectAgentChannelBot,
  useUpdateAgentChannelBot,
} from "@/hooks/use-agent-channel-bots"

/**
 * A provider dialog's open state. Opening uses the card's bot, else reserves
 * one first and works from it until the bot list has it.
 */
export function useChannelBotDialog({
  workspaceId,
  presetId,
  provider,
  bot,
}: {
  workspaceId: string
  presetId: string
  provider: ChannelBotProvider
  /** The card's bot from the list, or null. */
  bot: AgentChannelBotRead | null
}) {
  const { createBot, createBotIsPending } = useCreateAgentChannelBot(
    workspaceId,
    provider
  )
  const [open, setOpen] = useState(false)
  const [reserved, setReserved] = useState<AgentChannelBotRead | null>(null)

  function openDialog(): void {
    if (bot ?? reserved) {
      setOpen(true)
      return
    }
    // Reserved first, so the endpoint exists before the provider app.
    createBot(
      { agent_preset_id: presetId, provider },
      {
        onSuccess: (created) => {
          setReserved(created)
          setOpen(true)
        },
      }
    )
  }

  return {
    bot: bot ?? reserved,
    open,
    opening: createBotIsPending,
    openDialog,
    close: () => setOpen(false),
  }
}

/** What a user without `agent:update` sees instead of steps they cannot take. */
export const ASK_AN_AGENT_EDITOR =
  "Ask someone who can edit this agent to connect it again."

const STATE_ICONS: Record<ChannelBotState, LucideIcon> = {
  not_connected: CircleDashed,
  not_installed: CircleDashed,
  active: CircleCheck,
  paused: CirclePause,
  needs_reconnect: TriangleAlert,
}

/** A bot's state as an icon and label, or nothing before it is reserved. */
export function ChannelBotStatus({
  state,
  updateNeeded = false,
}: {
  state: ChannelBotState
  /** The install lacks scopes; shown over its run state. */
  updateNeeded?: boolean
}) {
  if (updateNeeded) {
    return (
      <StatusLabel icon={TriangleAlert} destructive>
        Update needed
      </StatusLabel>
    )
  }
  const { label } = CHANNEL_BOT_STATES[state]
  if (!label) {
    return null
  }
  return (
    <StatusLabel
      icon={STATE_ICONS[state]}
      destructive={state === "needs_reconnect"}
    >
      {label}
    </StatusLabel>
  )
}

/** A bot's status as a dot, for the Channels table and its sheet. */
export function ChannelBotStatusDot({
  state,
  updateNeeded = false,
}: {
  state: ChannelBotState
  updateNeeded?: boolean
}) {
  if (updateNeeded) {
    return <StatusDot tone="attention">Update needed</StatusDot>
  }
  const { label } = CHANNEL_BOT_STATES[state]
  if (!label) {
    return null
  }
  return <StatusDot tone={CHANNEL_BOT_STATES[state].tone}>{label}</StatusDot>
}

/** A provider row's icon, title, status and actions; wraps on narrow panels. */
export function ChannelBotHeader({
  providerId,
  title,
  description,
  status,
  children,
}: {
  providerId: string
  title: string
  description: ReactNode
  /** Usually a `ChannelBotStatus`; null shows none. */
  status: ReactNode
  /** The row's actions, after the status. */
  children?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-3 p-4">
      <ProviderIcon providerId={providerId} className="size-10 p-2" />
      <div className="min-w-48 flex-1">
        <p className="text-sm font-medium">{title}</p>
        <div className="text-xs text-muted-foreground">{description}</div>
      </div>
      <div className="ml-auto flex flex-wrap items-center gap-3">
        {status}
        {children}
      </div>
    </div>
  )
}

/** Something on a provider row that needs action, as a plain line. */
export function ChannelBotNotice({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  return (
    <div role="alert" className="flex items-start gap-2 text-xs">
      <TriangleAlert
        className="mt-px size-4 shrink-0 text-destructive"
        aria-hidden
      />
      <div className="space-y-0.5">
        <p className="font-medium">{title}</p>
        <p className="text-muted-foreground">{children}</p>
      </div>
    </div>
  )
}

/** A provider row in the Channels tab. */
export function ChannelBotCard({
  providerId,
  title,
  description,
  state,
  status,
  onManage,
  opening = false,
  children,
}: {
  providerId: string
  title: string
  description: ReactNode
  state: ChannelBotState
  /** Replaces the state's own status; notices own any reconnect action. */
  status?: ReactNode
  onManage: () => void
  opening?: boolean
  /** Provider notices and the provider's dialog. */
  children?: ReactNode
}) {
  const { installed } = CHANNEL_BOT_STATES[state]
  // Every channel bot request needs agent:update.
  const canUpdateAgent = useScopeCheck("agent:update") === true
  return (
    <div role="group" aria-label={title}>
      <ChannelBotHeader
        providerId={providerId}
        title={title}
        description={description}
        status={status ?? <ChannelBotStatus state={state} />}
      >
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={onManage}
          disabled={opening || !canUpdateAgent}
        >
          {opening ? <Loader2 className="mr-2 size-4 animate-spin" /> : null}
          {installed ? "Manage" : "Connect"}
        </Button>
      </ChannelBotHeader>
      {/* The dialog portals out, so a row with no notices stays empty. */}
      <div className="space-y-3 px-4 pb-4 empty:hidden">{children}</div>
    </div>
  )
}

/**
 * A provider's dialog, mounted only while open: what to paste and lifecycle
 * actions, then the form whose fields are `children`.
 */
export function ChannelBotDialog({
  onClose,
  busy,
  title,
  description,
  details,
  onSubmit,
  children,
  submitLabel,
  canSubmit,
  submitting,
  submitNote,
  secondaryAction,
}: {
  onClose: () => void
  /** A channel bot request is running; closing would drop its callbacks. */
  busy: boolean
  title: string
  description: string
  /** What to paste into the provider, and lifecycle actions. */
  details?: ReactNode
  onSubmit: () => void
  children: ReactNode
  submitLabel: string
  canSubmit: boolean
  submitting: boolean
  submitNote?: string | null
  secondaryAction?: ReactNode
}) {
  const formId = useId()
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) {
          onClose()
        }
      }}
    >
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="space-y-4">
            {details}
            {details && children ? <Separator /> : null}
            <form
              id={formId}
              className="space-y-4"
              onSubmit={(event) => {
                event.preventDefault()
                onSubmit()
              }}
            >
              {children}
            </form>
          </div>
        </DialogBody>
        <DialogFooter className="gap-3 sm:justify-between">
          <span className="text-xs text-muted-foreground">{submitNote}</span>
          <div className="flex items-center gap-2">
            {secondaryAction}
            <Button type="submit" size="sm" form={formId} disabled={!canSubmit}>
              {submitting ? (
                <Loader2 className="mr-2 size-4 animate-spin" />
              ) : null}
              {submitLabel}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** Pausing, resuming and disconnecting a bot, with their toasts. */
function useChannelBotLifecycle(
  workspaceId: string,
  bot: AgentChannelBotRead,
  onDisconnected?: () => void
) {
  const { updateBot } = useUpdateAgentChannelBot(workspaceId, bot.provider)
  const { disconnectBot } = useDisconnectAgentChannelBot(
    workspaceId,
    bot.provider
  )
  const busy = useChannelBotsBusy(workspaceId)
  const providerName = CHANNEL_BOT_PROVIDER_NAMES[bot.provider]

  function setRunState(status: "active" | "paused"): void {
    updateBot(
      { botId: bot.id, requestBody: { status } },
      {
        onSuccess: () =>
          toast({
            title: `${providerName} bot ${status === "paused" ? "paused" : "resumed"}`,
          }),
      }
    )
  }

  function disconnect(): void {
    disconnectBot(bot.id, {
      onSuccess: () => {
        onDisconnected?.()
        toast({ title: `${providerName} bot disconnected` })
      },
    })
  }

  return { busy, providerName, setRunState, disconnect }
}

const DEFAULT_DISCONNECT_DESCRIPTION =
  "The stored credentials are deleted and the bot stops answering until you connect it again."

function DisconnectConfirm({
  open,
  onOpenChange,
  providerName,
  description,
  onDisconnect,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  providerName: string
  description: string
  onDisconnect: () => void
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Disconnect {providerName}?</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={onDisconnect}>
            Disconnect
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

/**
 * Pause, resume and disconnect for an installed bot. `split` puts Disconnect
 * on the right as a quiet destructive link, after `leading` and the run toggle.
 */
export function ChannelBotLifecycleActions({
  workspaceId,
  bot,
  state,
  onDisconnected,
  disconnectDescription = DEFAULT_DISCONNECT_DESCRIPTION,
  layout = "row",
  leading,
}: {
  workspaceId: string
  bot: AgentChannelBotRead
  state: ChannelBotState
  onDisconnected?: () => void
  disconnectDescription?: string
  layout?: "row" | "split"
  /** Actions before Pause or Resume, such as Reconnect. */
  leading?: ReactNode
}) {
  const { busy, providerName, setRunState, disconnect } =
    useChannelBotLifecycle(workspaceId, bot, onDisconnected)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const split = layout === "split"
  const icons = !split

  let runToggle: ReactNode = null
  if (state === "active") {
    runToggle = (
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={() => setRunState("paused")}
        disabled={busy}
      >
        {icons ? <Pause className="mr-2 size-4" /> : null}
        Pause
      </Button>
    )
  } else if (state === "paused") {
    runToggle = (
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={() => setRunState("active")}
        disabled={busy}
      >
        {icons ? <Play className="mr-2 size-4" /> : null}
        Resume
      </Button>
    )
  }
  const disconnectButton = (
    <Button
      type="button"
      size="sm"
      variant={split ? "ghost" : "outline"}
      className={split ? "text-destructive hover:text-destructive" : undefined}
      onClick={() => setConfirmOpen(true)}
      disabled={busy}
    >
      {icons ? <Unplug className="mr-2 size-4" /> : null}
      Disconnect
    </Button>
  )

  return (
    <div
      className={
        split
          ? "flex items-center justify-between gap-2"
          : "flex flex-wrap gap-2"
      }
    >
      {split ? (
        <div className="flex items-center gap-2">
          {leading}
          {runToggle}
        </div>
      ) : (
        <>
          {leading}
          {runToggle}
        </>
      )}
      {disconnectButton}
      <DisconnectConfirm
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        providerName={providerName}
        description={disconnectDescription}
        onDisconnect={disconnect}
      />
    </div>
  )
}
