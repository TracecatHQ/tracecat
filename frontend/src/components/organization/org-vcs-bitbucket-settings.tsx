"use client"

import { useState } from "react"
import { BitbucketTokenSetup } from "@/components/organization/org-vcs-bitbucket"
import { BitbucketDataCenterTokenSetup } from "@/components/organization/org-vcs-bitbucket-data-center"
import { BitbucketIcon } from "@/components/organization/vcs-icons"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useBitbucketTokenCredentialsStatus } from "@/hooks/use-bitbucket-credentials"
import { useBitbucketDataCenterTokenCredentialsStatus } from "@/hooks/use-bitbucket-data-center-credentials"

/** Present one provider while keeping both deployment credentials manageable. */
export function BitbucketSettings() {
  const [open, setOpen] = useState(false)
  const cloud = useBitbucketTokenCredentialsStatus()
  const dataCenter = useBitbucketDataCenterTokenCredentialsStatus()
  const statuses = [cloud, dataCenter]
  const configured = statuses.some((s) => s.credentialsStatus?.exists)
  let statusLabel = "Not connected"
  if (statuses.some((s) => s.credentialsStatusIsLoading)) {
    statusLabel = "Loading connections…"
  } else if (statuses.some((s) => s.credentialsStatusError)) {
    statusLabel = "Unable to load all connections"
  } else if (statuses.some((s) => s.credentialsStatus?.is_corrupted)) {
    statusLabel = "A connection needs attention"
  } else if (configured) {
    statusLabel = "Connected"
  }

  return (
    <>
      <div className="flex items-center justify-between rounded-lg border p-4">
        <div className="flex items-center gap-3">
          <BitbucketIcon className="size-5 text-muted-foreground" />
          <div>
            <p className="text-sm font-medium">Bitbucket</p>
            <p className="text-xs text-muted-foreground">{statusLabel}</p>
          </div>
        </div>
        <Button
          size="sm"
          variant={configured ? "outline" : "default"}
          onClick={() => setOpen(true)}
        >
          {configured || statuses.some((s) => s.credentialsStatusError)
            ? "Manage"
            : "Connect"}
        </Button>
      </div>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>Bitbucket connections</DialogTitle>
            <DialogDescription>
              Connect the Bitbucket service your repositories use. You can
              configure both.
            </DialogDescription>
          </DialogHeader>
          {open && (
            <div className="space-y-4">
              <BitbucketTokenSetup />
              <BitbucketDataCenterTokenSetup />
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}
