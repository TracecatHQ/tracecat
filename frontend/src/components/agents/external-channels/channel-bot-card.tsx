"use client"

import { Loader2 } from "lucide-react"
import { type ReactNode, useId, useState } from "react"
import type { AgentChannelBotRead, ChannelBotProvider } from "@/client"
import {
  CHANNEL_BOT_PROVIDER_NAMES,
  CHANNEL_BOT_STATES,
  type ChannelBotState,
} from "@/components/agents/external-channels/channel-bot-utils"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { ProviderIcon } from "@/components/icons"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
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
import { Badge } from "@/components/ui/badge"
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

/** A provider row in the Channels tab. */
export function ChannelBotCard({
  providerId,
  title,
  description,
  state,
  reconnectHint,
  onManage,
  opening = false,
  children,
}: {
  providerId: string
  title: string
  description: ReactNode
  state: ChannelBotState
  /** What restores a bot that needs reconnecting. */
  reconnectHint: string
  onManage: () => void
  opening?: boolean
  /** Provider notices and the provider's dialog. */
  children?: ReactNode
}) {
  const { label, installed } = CHANNEL_BOT_STATES[state]
  // Every channel bot request needs agent:update.
  const canUpdateAgent = useScopeCheck("agent:update") === true
  return (
    <>
      <div className="rounded border" role="group" aria-label={title}>
        <div className="flex items-center gap-4 p-4">
          <div className="flex size-10 items-center justify-center rounded border bg-muted/30">
            <ProviderIcon providerId={providerId} className="size-7 p-0.5" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium">{title}</p>
            <p className="text-xs text-muted-foreground">{description}</p>
          </div>
          {label ? <Badge variant="secondary">{label}</Badge> : null}
          <Button
            type="button"
            size="sm"
            onClick={onManage}
            disabled={opening || !canUpdateAgent}
          >
            {opening ? <Loader2 className="mr-2 size-4 animate-spin" /> : null}
            {installed ? "Manage" : "Connect"}
          </Button>
        </div>
      </div>
      {state === "needs_reconnect" ? (
        <Alert>
          <AlertTitle>Reconnect needed</AlertTitle>
          <AlertDescription>{reconnectHint}</AlertDescription>
        </Alert>
      ) : null}
      {children}
    </>
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
            {details ? <Separator /> : null}
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

/** Pause, resume and disconnect for an installed bot. */
export function ChannelBotLifecycleActions({
  workspaceId,
  bot,
  state,
  onDisconnected,
}: {
  workspaceId: string
  bot: AgentChannelBotRead
  state: ChannelBotState
  onDisconnected: () => void
}) {
  const { updateBot } = useUpdateAgentChannelBot(workspaceId, bot.provider)
  const { disconnectBot } = useDisconnectAgentChannelBot(
    workspaceId,
    bot.provider
  )
  const busy = useChannelBotsBusy(workspaceId)
  const [confirmOpen, setConfirmOpen] = useState(false)
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
        onDisconnected()
        toast({ title: `${providerName} bot disconnected` })
      },
    })
  }

  return (
    <div className="flex flex-wrap gap-2">
      {state === "active" ? (
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => setRunState("paused")}
          disabled={busy}
        >
          Pause
        </Button>
      ) : null}
      {state === "paused" ? (
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => setRunState("active")}
          disabled={busy}
        >
          Resume
        </Button>
      ) : null}
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={() => setConfirmOpen(true)}
        disabled={busy}
      >
        Disconnect
      </Button>
      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Disconnect {providerName}?</AlertDialogTitle>
            <AlertDialogDescription>
              The stored credentials are deleted and the bot stops answering
              until you connect it again.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={disconnect}>
              Disconnect
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
