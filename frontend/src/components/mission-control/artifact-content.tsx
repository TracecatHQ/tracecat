"use client"

import { ExternalLink } from "lucide-react"
import Link from "next/link"
import { CasePanelView } from "@/components/cases/case-panel-view"
import {
  getArtifactConfig,
  getArtifactHref,
} from "@/components/mission-control/artifact-registry"
import { ArtifactIcon } from "@/components/mission-control/artifact-tabs"
import { AlertNotification } from "@/components/notifications"
import { TablePanelProvider } from "@/components/tables/table-panel-context"
import { TableSelectionProvider } from "@/components/tables/table-selection-context"
import { DatabaseTable } from "@/components/tables/table-view"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { useGetTable } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import type { MissionControlArtifact } from "@/types/mission-control"

export interface ArtifactContentProps {
  artifact: MissionControlArtifact
  workspaceId: string
}

/** Render the active artifact content with type-specific embedded views. */
export function ArtifactContent({
  artifact,
  workspaceId,
}: ArtifactContentProps) {
  switch (artifact.type) {
    case "case":
      return <CasePanelView caseId={artifact.id} embedded />
    case "table":
      return (
        <EmbeddedTableArtifact artifact={artifact} workspaceId={workspaceId} />
      )
    default:
      return <ArtifactSummary artifact={artifact} workspaceId={workspaceId} />
  }
}

function EmbeddedTableArtifact({
  artifact,
  workspaceId,
}: {
  artifact: Extract<MissionControlArtifact, { type: "table" }>
  workspaceId: string
}) {
  const { table, tableIsLoading, tableError } = useGetTable({
    tableId: artifact.id,
    workspaceId,
  })

  if (tableIsLoading) {
    return (
      <div className="flex h-full flex-col gap-3 p-4">
        <Skeleton className="h-5 w-1/2" />
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-2/3" />
      </div>
    )
  }

  if (tableError || !table) {
    return (
      <div className="p-4">
        <AlertNotification
          message={tableError?.message ?? "Error loading table"}
          variant="error"
        />
      </div>
    )
  }

  return (
    <div className="size-full overflow-hidden">
      <TableSelectionProvider>
        <TablePanelProvider>
          <DatabaseTable table={table} />
        </TablePanelProvider>
      </TableSelectionProvider>
    </div>
  )
}

function ArtifactSummary({
  artifact,
  workspaceId,
}: {
  artifact: MissionControlArtifact
  workspaceId: string
}) {
  const config = getArtifactConfig(artifact)
  const href = getArtifactHref(artifact, workspaceId)

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 border-b px-4 py-3">
        <div className="flex items-center gap-2">
          <ArtifactIcon artifact={artifact} icon={config.icon} />
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">{artifact.title}</p>
            <p className="text-xs text-muted-foreground">
              {config.singularLabel}
            </p>
          </div>
        </div>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-4 p-4 text-sm">
          <ArtifactFields artifact={artifact} />
          {href ? (
            <Button size="sm" variant="outline" className="h-8 gap-1.5" asChild>
              <Link href={href}>
                <ExternalLink className="size-3.5" />
                Open full view
              </Link>
            </Button>
          ) : null}
        </div>
      </ScrollArea>
    </div>
  )
}

function ArtifactFields({ artifact }: { artifact: MissionControlArtifact }) {
  switch (artifact.type) {
    case "workflow":
      return (
        <dl className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-2">
          <ArtifactField
            label="Published"
            value={artifact.isPublished ? "yes" : "no"}
          />
          <ArtifactField label="Workflow ID" value={artifact.id} monospace />
        </dl>
      )
    case "run":
      return (
        <dl className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-2">
          <ArtifactField label="Status" value={artifact.status} />
          <ArtifactField label="Started" value={artifact.startedAt} />
          <ArtifactField label="Run ID" value={artifact.id} monospace />
        </dl>
      )
    case "generic":
      return (
        <pre className="max-h-96 overflow-auto rounded-sm border bg-muted/30 p-3 text-xs">
          {JSON.stringify(artifact.data ?? {}, null, 2)}
        </pre>
      )
    default:
      return (
        <dl className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-2">
          <ArtifactField label="ID" value={artifact.id} monospace />
        </dl>
      )
  }
}

function ArtifactField({
  label,
  value,
  monospace = false,
}: {
  label: string
  value: string | number
  monospace?: boolean
}) {
  return (
    <>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd
        className={cn("min-w-0 break-words text-xs", monospace && "font-mono")}
      >
        {String(value).replaceAll("_", " ")}
      </dd>
    </>
  )
}
