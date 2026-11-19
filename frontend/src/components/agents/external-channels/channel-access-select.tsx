"use client"

import type { AgentChannelBotRead, ChannelAccessPolicy } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  useChannelBotsBusy,
  useUpdateAgentChannelBot,
} from "@/hooks/use-agent-channel-bots"

const POLICIES: ReadonlyArray<[ChannelAccessPolicy, string]> = [
  ["linked_approvals", "Anyone can message; linked members approve"],
  ["linked_only", "Only linked members"],
  ["open", "Anyone in the channel"],
]

/** One rule for every channel and agent the bot serves. */
export function ChannelAccessSelect({
  workspaceId,
  bot,
}: {
  workspaceId: string
  bot: AgentChannelBotRead
}) {
  const canUpdateAgent = useScopeCheck("agent:update") === true
  const { updateBot } = useUpdateAgentChannelBot(workspaceId, bot.provider)
  const busy = useChannelBotsBusy(workspaceId)
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs font-medium">Who can use this bot</span>
      <Select
        value={bot.channel_access_policy}
        onValueChange={(value) => {
          const policy = POLICIES.find(([option]) => option === value)?.[0]
          if (policy) {
            updateBot({
              botId: bot.id,
              requestBody: { channel_access_policy: policy },
            })
          }
        }}
        disabled={!canUpdateAgent || busy}
      >
        <SelectTrigger
          className="h-8 text-xs"
          aria-label="Who can use this bot"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {POLICIES.map(([value, label]) => (
            <SelectItem key={value} value={value} className="text-xs">
              {label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <p className="text-xs text-muted-foreground">
        Members link their channel account by signing in to Tracecat.
      </p>
    </div>
  )
}
