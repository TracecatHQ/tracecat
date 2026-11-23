"use client"

import { ArrowUpRight, Loader2, Plus } from "lucide-react"
import { useRouter } from "next/navigation"
import { type ReactNode, useState } from "react"
import type { AgentChannelBotRead, AgentPresetReadMinimal } from "@/client"
import {
  channelBotReturnUrl,
  goToChannelBotAuthorization,
} from "@/components/agents/external-channels/channel-bot-utils"
import { AgentSelect } from "@/components/agents/external-channels/platform-channel-routes"
import { ProviderIcon } from "@/components/icons"
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  useChannelBotsBusy,
  useInstallPlatformChannelBot,
} from "@/hooks/use-agent-channel-bots"
import { useAgentPresets } from "@/hooks/use-agent-presets"

/** Which way in the Add bot menu or the empty table opened. */
export type ChannelBotChoice = "tracecat" | "custom"

/** Whether this workspace can add Tracecat for Slack now. */
export type PlatformOption = "ready" | "missing" | "installed" | "failed"

const PLATFORM_HELP: Record<PlatformOption, string> = {
  ready: "One click. Answers as @Tracecat; pick agents per channel.",
  missing:
    "Not set up on this Tracecat. A platform admin sets it up in Admin › Slack app.",
  installed: "Already added to this workspace's Slack.",
  failed: "Couldn't check whether Tracecat is set up here.",
}

function MenuOption({
  icon,
  title,
  help,
  disabled = false,
  onSelect,
}: {
  icon: ReactNode
  title: string
  help: string
  disabled?: boolean
  onSelect?: () => void
}) {
  return (
    <DropdownMenuItem
      className="items-start gap-2.5 text-xs"
      disabled={disabled}
      onSelect={onSelect}
    >
      {icon}
      <div className="space-y-0.5">
        <p className="font-medium">{title}</p>
        <p className="text-muted-foreground">{help}</p>
      </div>
    </DropdownMenuItem>
  )
}

/** Every way to add a bot: Tracecat, a custom Slack app, and later Teams. */
export function AddChannelBotMenu({
  platform,
  onChoose,
}: {
  platform: PlatformOption
  onChoose: (choice: ChannelBotChoice) => void
}) {
  const slackIcon = <ProviderIcon providerId="slack" className="size-4 p-0" />
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button type="button" size="sm">
          <Plus className="mr-2 size-4" />
          Add bot
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80">
        <MenuOption
          icon={slackIcon}
          title="Tracecat"
          help={PLATFORM_HELP[platform]}
          disabled={platform !== "ready"}
          onSelect={() => onChoose("tracecat")}
        />
        <MenuOption
          icon={slackIcon}
          title="Custom Slack app"
          help="Your own name and icon. You create the app in Slack from a manifest we give you."
          onSelect={() => onChoose("custom")}
        />
        <MenuOption
          icon={
            <ProviderIcon providerId="microsoft_teams" className="size-4 p-0" />
          }
          title="Microsoft Teams"
          help="Not available yet."
          disabled
        />
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** The table before any bot: the common ways in, or who to ask. */
export function NoChannelBots({
  platform,
  canUpdateAgent,
  onChoose,
}: {
  platform: PlatformOption
  canUpdateAgent: boolean
  onChoose: (choice: ChannelBotChoice) => void
}) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-md border px-6 py-14 text-center">
      <ProviderIcon providerId="slack" className="size-7 bg-transparent p-0" />
      <h3 className="text-sm font-semibold">No bots yet</h3>
      <p className="max-w-md text-xs text-muted-foreground">
        Add Tracecat to Slack so people can talk to your agents in DMs and
        channels.
      </p>
      {canUpdateAgent ? (
        <div className="mt-2 flex flex-wrap justify-center gap-2">
          {platform === "ready" ? (
            <Button
              type="button"
              size="sm"
              onClick={() => onChoose("tracecat")}
            >
              Add Tracecat
            </Button>
          ) : null}
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => onChoose("custom")}
          >
            Use a custom Slack app
          </Button>
        </div>
      ) : (
        <p className="text-xs">
          Ask someone who can edit agents to add Tracecat to Slack.
        </p>
      )}
      {platform === "missing" ? (
        <p className="mt-1 max-w-md text-xs text-muted-foreground">
          Tracecat for Slack isn&apos;t set up on this instance yet. A platform
          admin sets it up in Admin › Slack app.
        </p>
      ) : null}
    </div>
  )
}

function PickAgentDialog({
  title,
  description,
  label,
  hint,
  agents,
  initialAgentId,
  submitLabel,
  submitting,
  busy,
  onSubmit,
  onClose,
}: {
  title: string
  description: string
  label: string
  hint: string
  agents: AgentPresetReadMinimal[]
  initialAgentId: string
  submitLabel: ReactNode
  submitting: boolean
  busy: boolean
  onSubmit: (agentPresetId: string) => void
  onClose: () => void
}) {
  const [chosen, setChosen] = useState<string | null>(null)
  // A chosen agent missing from the list counts as no choice.
  const agentId = agents.some((agent) => agent.id === chosen)
    ? (chosen ?? "")
    : initialAgentId
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) {
          onClose()
        }
      }}
    >
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="space-y-1.5">
            <p className="text-xs font-medium">{label}</p>
            <AgentSelect
              label={label}
              value={agentId}
              agents={agents}
              disabled={busy}
              onChange={setChosen}
            />
            <p className="text-xs text-muted-foreground">{hint}</p>
          </div>
        </DialogBody>
        <DialogFooter>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={onClose}
            disabled={busy}
          >
            Cancel
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={() => onSubmit(agentId)}
            disabled={busy || !agentId}
          >
            {submitting ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : null}
            {submitLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** Picks the agent for DMs and unlisted channels, then sends the browser to Slack. */
export function AddTracecatDialog({
  workspaceId,
  install,
  onClose,
}: {
  workspaceId: string
  /** A disconnected or unfinished install to add again, or null. */
  install: AgentChannelBotRead | null
  onClose: () => void
}) {
  const { presets } = useAgentPresets(workspaceId, { useInChat: true })
  const { installPlatformBot, installPlatformBotIsPending } =
    useInstallPlatformChannelBot(workspaceId, "slack")
  const busy = useChannelBotsBusy(workspaceId)
  const agents = presets ?? []
  // Adding an install again offers its last default agent.
  const previousAgentId =
    install && agents.some((agent) => agent.id === install.agent_preset_id)
      ? install.agent_preset_id
      : ""
  return (
    <PickAgentDialog
      title="Add Tracecat"
      description="People talk to your agents as @Tracecat. You approve the install in Slack, then come back here."
      label="Agent for DMs and unlisted channels"
      hint={
        agents.length > 0 || !presets
          ? "Lists agents with Use in chat on."
          : "No agent has Use in chat on. Turn it on for an agent first."
      }
      agents={agents}
      initialAgentId={previousAgentId}
      submitLabel={
        <>
          Continue in Slack
          <ArrowUpRight className="ml-2 size-4" />
        </>
      }
      submitting={installPlatformBotIsPending}
      busy={busy}
      onClose={onClose}
      onSubmit={(agentPresetId) =>
        installPlatformBot(
          {
            provider: "slack",
            return_url: channelBotReturnUrl(),
            bot_id: install?.id,
            agent_preset_id: agentPresetId,
          },
          {
            onSuccess: ({ authorization_url }) => {
              if (authorization_url) {
                goToChannelBotAuthorization(authorization_url)
              }
            },
          }
        )
      }
    />
  )
}

/** Picks the agent a custom Slack app answers for, then opens its Channels tab. */
export function CustomSlackAppDialog({
  workspaceId,
  onClose,
}: {
  workspaceId: string
  onClose: () => void
}) {
  const router = useRouter()
  const { presets } = useAgentPresets(workspaceId)
  return (
    <PickAgentDialog
      title="Use a custom Slack app"
      description="The app gets its own name and icon, and answers only for one agent. You create it in Slack from that agent's Channels tab."
      label="Agent"
      hint="The app answers for this agent in every channel it is in."
      agents={presets ?? []}
      initialAgentId=""
      submitLabel="Continue"
      submitting={false}
      busy={false}
      onClose={onClose}
      onSubmit={(agentPresetId) =>
        router.push(
          `/workspaces/${workspaceId}/agents/${agentPresetId}?tab=channels`
        )
      }
    />
  )
}
