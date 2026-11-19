"use client"

import { useState } from "react"
import type { AgentChannelUserRead } from "@/client"
import {
  ChannelProviderIcon,
  channelProviderName,
} from "@/components/agents/channel-provider"
import { CenteredSpinner } from "@/components/loading/spinner"
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
import { useMyChannelUsers } from "@/hooks/use-channel-users"

export function LinkedAccountsSettings() {
  const { links, isLoading, error, unlink, unlinkPending } = useMyChannelUsers()
  const [unlinkTarget, setUnlinkTarget] = useState<AgentChannelUserRead | null>(
    null
  )

  if (isLoading) {
    return <CenteredSpinner />
  }

  const targetName = unlinkTarget
    ? (channelProviderName(unlinkTarget.provider) ?? unlinkTarget.provider)
    : ""
  return (
    <div className="space-y-8">
      <div className="space-y-1">
        <h3 className="text-lg font-semibold">Linked accounts</h3>
        <p className="text-sm text-muted-foreground">
          Chat accounts that act as you when you message a Tracecat agent.
        </p>
      </div>

      <div className="divide-y rounded-md border">
        {links && links.length > 0 ? (
          links.map((link) => {
            const name = channelProviderName(link.provider) ?? link.provider
            return (
              <div
                key={link.id}
                className="flex items-center justify-between gap-4 p-3"
              >
                <div className="flex min-w-0 items-center gap-3">
                  <ChannelProviderIcon
                    provider={link.provider}
                    className="size-4 shrink-0"
                  />
                  <div className="min-w-0">
                    <span className="truncate text-sm font-medium">{name}</span>
                    <p className="text-xs text-muted-foreground">
                      {link.provider_email ?? "Linked"}
                    </p>
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={`Unlink ${name}`}
                  disabled={unlinkPending}
                  onClick={() => setUnlinkTarget(link)}
                >
                  Unlink
                </Button>
              </div>
            )
          })
        ) : error ? (
          <p className="p-3 text-sm text-destructive">
            Couldn't load linked accounts.
          </p>
        ) : (
          <p className="p-3 text-sm text-muted-foreground">
            No linked accounts. Sign in from a Slack message to link one.
          </p>
        )}
      </div>

      <AlertDialog
        open={unlinkTarget !== null}
        onOpenChange={(open) => !open && setUnlinkTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Unlink {targetName} account?</AlertDialogTitle>
            <AlertDialogDescription>
              {`Messages you send in ${targetName} will no longer count as you. You can link it again from a ${targetName} message.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={unlinkPending}
              onClick={async () => {
                if (!unlinkTarget) return
                try {
                  await unlink(unlinkTarget.id)
                } catch {
                  // The mutation shows the error toast.
                }
                setUnlinkTarget(null)
              }}
            >
              Unlink
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
