"use client"

import { DiffIcon } from "lucide-react"
import { useState } from "react"
import type { tracecat__registry__repositories__schemas__RegistryVersionRead } from "@/client"
import { DiffView } from "@/components/registry/version-diff-view"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { ScrollArea } from "@/components/ui/scroll-area"
import { useRegistryVersionDiff } from "@/lib/hooks"

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
      <DialogContent className="flex max-h-[80vh] max-w-3xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="p-6 pb-4">
          <DialogTitle className="flex items-center gap-2">
            <DiffIcon className="size-5" />
            Compare versions
          </DialogTitle>
          <DialogDescription>
            View the actions that changed between two versions.
          </DialogDescription>
        </DialogHeader>
        <ScrollArea className="flex-1 px-6 pb-6">
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
        </ScrollArea>
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
  const { diff, diffIsLoading } = useRegistryVersionDiff(
    repositoryId,
    baseId,
    compareToId
  )

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
