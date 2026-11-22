"use client"

import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useSessionChannelSync } from "@/hooks/use-session-channel-sync"

/** The session header's switch for syncing the session with its channel thread. */
export function ChannelSyncToggle({
  workspaceId,
  sessionId,
}: {
  workspaceId: string
  sessionId: string
}) {
  const { sync, setEnabled, isPending } = useSessionChannelSync(
    workspaceId,
    sessionId
  )
  if (!sync?.available) {
    return null
  }
  const id = `channel-sync-${sessionId}`
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div className="flex items-center gap-1.5 px-1">
          <Switch
            id={id}
            size="xs"
            checked={sync.enabled}
            disabled={isPending}
            onCheckedChange={(enabled) => setEnabled(enabled)}
          />
          <Label
            htmlFor={id}
            className="cursor-pointer text-xs font-normal text-muted-foreground"
          >
            Sync with channel
          </Label>
        </div>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-64">
        Messages sent here post to the agent's channel thread. Earlier messages
        are not posted.
      </TooltipContent>
    </Tooltip>
  )
}
