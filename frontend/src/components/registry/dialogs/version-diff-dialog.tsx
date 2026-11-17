"use client"

import { DiffIcon } from "lucide-react"
import { useState } from "react"
import type { tracecat__registry__repositories__schemas__RegistryVersionRead } from "@/client"
import { DiffView } from "@/components/registry/version-diff-view"
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useRegistryVersionDiff } from "@/hooks/use-registry-versions"

type RegistryVersionRead =
  tracecat__registry__repositories__schemas__RegistryVersionRead

/** Props for {@link VersionDiffDialog}. */
export interface VersionDiffDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  repositoryId: string | null
  versions: RegistryVersionRead[]
  initialBaseId: string | null
  initialCompareId: string | null
}

/** Dialog that compares the actions of two registry versions. */
export function VersionDiffDialog({
  open,
  onOpenChange,
  repositoryId,
  versions,
  initialBaseId,
  initialCompareId,
}: VersionDiffDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg" className="max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <DiffIcon className="size-5" />
            Compare versions
          </DialogTitle>
          <DialogDescription>
            View the actions that changed between two versions.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div>
            {open && (
              <VersionDiffContent
                // Remount so a new selection resets the pickers.
                key={`${initialBaseId ?? ""}:${initialCompareId ?? ""}`}
                repositoryId={repositoryId}
                versions={versions}
                initialBaseId={initialBaseId}
                initialCompareId={initialCompareId}
              />
            )}
          </div>
        </DialogBody>
      </DialogContent>
    </Dialog>
  )
}

function VersionDiffContent({
  repositoryId,
  versions,
  initialBaseId,
  initialCompareId,
}: Omit<VersionDiffDialogProps, "open" | "onOpenChange">) {
  const [baseId, setBaseId] = useState<string | null>(initialBaseId)
  const [compareToId, setCompareToId] = useState<string | null>(
    initialCompareId
  )
  const { diff, diffIsLoading } = useRegistryVersionDiff({
    repositoryId,
    baseId,
    compareToId,
  })

  return (
    <DiffView
      diff={diff ?? null}
      diffLoading={diffIsLoading}
      versions={versions}
      compareBaseId={baseId}
      compareToId={compareToId}
      onBaseChange={setBaseId}
      onCompareChange={setCompareToId}
    />
  )
}
