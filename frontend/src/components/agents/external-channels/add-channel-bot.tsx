"use client"

import { ArrowUpRight, Info, Loader2, Plus } from "lucide-react"
import { useRouter } from "next/navigation"
import { type ReactNode, useState } from "react"
import type { AgentChannelBotRead, AgentPresetReadMinimal } from "@/client"
import { AgentSelect } from "@/components/agents/external-channels/channel-bot-fields"
import {
  channelBotReturnUrl,
  goToChannelBotAuthorization,
} from "@/components/agents/external-channels/channel-bot-utils"
import { ChannelsSideSheet } from "@/components/agents/external-channels/channels-side-sheet"
import { ProviderIcon } from "@/components/icons"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  useChannelBotsBusy,
  useInstallPlatformChannelBot,
} from "@/hooks/use-agent-channel-bots"
import { useAgentPresets } from "@/hooks/use-agent-presets"

/** Which way in the Add bot menu or the empty table opened. */
export type ChannelBotChoice = "tracecat" | "custom"

/** Whether this workspace can add Tracecat for Slack now. */
export type PlatformOption =
  | "ready"
  | "checking"
  | "missing"
  | "installed"
  | "failed"

const PLATFORM_HELP: Record<PlatformOption, string> = {
  ready: "One click. Answers as @Tracecat; pick agents per channel.",
  missing:
    "Not set up on this Tracecat. A platform admin sets it up in Admin › Slack app.",
  installed: "Already added to this workspace's Slack.",
  checking: "Checking whether Tracecat is already added…",
  failed: "Couldn't check whether Tracecat is set up here.",
}

const USE_IN_CHAT_TIP =
  "Don't see an agent? Turn on Use in chat in its settings."

/** Marks Tracecat as the suggested way in; neutral, no accent. */
function RecommendedBadge() {
  return (
    <Badge
      variant="outline"
      className="rounded px-1.5 py-0 text-[11px] font-normal text-muted-foreground"
    >
      Recommended
    </Badge>
  )
}

function MenuOption({
  icon,
  title,
  badge,
  help,
  disabled = false,
  onSelect,
}: {
  icon: ReactNode
  title: string
  badge?: ReactNode
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
        <div className="flex items-center gap-1.5 font-medium">
          {title}
          {badge}
        </div>
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
          badge={<RecommendedBadge />}
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
            <div className="flex items-center gap-1.5">
              <Button
                type="button"
                size="sm"
                onClick={() => onChoose("tracecat")}
              >
                Add Tracecat
              </Button>
              <RecommendedBadge />
            </div>
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

/** The side sheet's add-a-bot body: one agent, then on to the install. */
function PickAgentSheet({
  title,
  description,
  label,
  labelTip,
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
  /** Shown from an info icon beside the label. */
  labelTip?: string
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
    <ChannelsSideSheet
      label={title}
      providerId="slack"
      title={title}
      onClose={onClose}
      closeDisabled={busy}
      footer={
        <>
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
        </>
      }
    >
      <div className="space-y-5">
        <p className="text-xs text-muted-foreground">{description}</p>
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5">
            <p className="text-xs font-medium">{label}</p>
            {labelTip ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    aria-label={labelTip}
                    className="rounded-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
                  >
                    <Info className="size-3.5" />
                  </button>
                </TooltipTrigger>
                <TooltipContent className="max-w-60">{labelTip}</TooltipContent>
              </Tooltip>
            ) : null}
          </div>
          <AgentSelect
            label={label}
            value={agentId}
            agents={agents}
            disabled={busy}
            onChange={setChosen}
            className="w-full"
          />
          <p className="text-xs text-muted-foreground">{hint}</p>
        </div>
      </div>
    </ChannelsSideSheet>
  )
}

/** Picks the agent for DMs and unlisted channels, then sends the browser to Slack. */
export function AddTracecatSheet({
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
  const noEligibleAgents = presets !== undefined && agents.length === 0
  // Adding an install again offers its last default agent.
  const previousAgentId =
    install && agents.some((agent) => agent.id === install.agent_preset_id)
      ? install.agent_preset_id
      : ""
  return (
    <PickAgentSheet
      title="Add Tracecat"
      description="People talk to your agents as @Tracecat. You approve the install in Slack, then come back here."
      label="Agent for DMs and unlisted channels"
      labelTip={noEligibleAgents ? undefined : USE_IN_CHAT_TIP}
      hint={
        noEligibleAgents ? USE_IN_CHAT_TIP : "Lists agents with Use in chat on."
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
export function CustomSlackAppSheet({
  workspaceId,
  onClose,
}: {
  workspaceId: string
  onClose: () => void
}) {
  const router = useRouter()
  const { presets } = useAgentPresets(workspaceId)
  return (
    <PickAgentSheet
      title="Custom Slack app"
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
