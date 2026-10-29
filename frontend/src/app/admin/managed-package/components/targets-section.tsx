"use client"

import { MoreHorizontalIcon, PlusIcon } from "lucide-react"
import { useState } from "react"
import type {
  GitTagInfo,
  ManagedRolloutRead,
  ManagedTargetRead,
} from "@/client"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { toast } from "@/components/ui/use-toast"
import { getApiErrorDetail } from "@/lib/errors"
import { getRelativeTime } from "@/lib/event-history"
import { toastApiError } from "../managed-package-utils"
import {
  useApplyRollouts,
  usePreviewRollouts,
  useTrackedRollouts,
  useUnassignTarget,
} from "../use-managed-package"
import { AssignTargetDialog } from "./assign-target-dialog"
import { BumpControls } from "./bump-controls"
import { ChangePinDialog } from "./change-pin-dialog"
import { ReferenceMatchDialog } from "./reference-match-dialog"
import { RolloutDetailSheet } from "./rollout-detail-sheet"
import { RolloutProgress } from "./rollout-progress"
import { TargetStatusBadge } from "./status-badge"
import { UnassignTargetDialog } from "./unassign-target-dialog"

/** Props for {@link TargetsSection}. */
export interface TargetsSectionProps {
  targets: ManagedTargetRead[]
  tags: GitTagInfo[]
  isLoading: boolean
  error: unknown
  hasSource: boolean
}

/**
 * Target workspaces, with the version bump flow: select targets, pick a tag,
 * preview, review each target, then apply the ready ones.
 */
export function TargetsSection({
  targets,
  tags,
  isLoading,
  error,
  hasSource,
}: TargetsSectionProps) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [assignOpen, setAssignOpen] = useState(false)
  const [pinTarget, setPinTarget] = useState<ManagedTargetRead | null>(null)
  const [referencesTarget, setReferencesTarget] =
    useState<ManagedTargetRead | null>(null)
  const [unassign, setUnassign] = useState<{
    rolloutId: string
    target: ManagedTargetRead
  } | null>(null)
  // Snapshots of the rollouts started here; live data comes from polling.
  const [tracked, setTracked] = useState<ManagedRolloutRead[]>([])
  const [detailRolloutId, setDetailRolloutId] = useState<string | null>(null)

  const previewRollouts = usePreviewRollouts()
  const applyRollouts = useApplyRollouts()
  const unassignTarget = useUnassignTarget()
  const live = useTrackedRollouts(tracked.map((rollout) => rollout.id))

  const liveById = new Map(
    live.rollouts.map((rollout) => [rollout.id, rollout])
  )
  const rollouts = tracked.map((rollout) => liveById.get(rollout.id) ?? rollout)
  const targetsById = new Map(targets.map((target) => [target.id, target]))
  const selectedTargets = targets.filter((target) => selectedIds.has(target.id))
  const detailRollout =
    rollouts.find((rollout) => rollout.id === detailRolloutId) ?? null

  const allSelected =
    targets.length > 0 && selectedTargets.length === targets.length
  let headerChecked: boolean | "indeterminate" = false
  if (allSelected) {
    headerChecked = true
  } else if (selectedTargets.length > 0) {
    headerChecked = "indeterminate"
  }

  function toggleTarget(targetId: string, checked: boolean) {
    setSelectedIds((current) => {
      const next = new Set(current)
      if (checked) {
        next.add(targetId)
      } else {
        next.delete(targetId)
      }
      return next
    })
  }

  async function startPreview(targetIds: string[], tag: string | null) {
    try {
      const created = await previewRollouts.mutateAsync({ targetIds, tag })
      // A new preview supersedes the target's earlier one, so replace it.
      setTracked((current) => [
        ...current.filter(
          (rollout) => !targetIds.includes(rollout.target_id ?? "")
        ),
        ...created,
      ])
    } catch (err) {
      toastApiError("Failed to start preview", err)
    }
  }

  async function handleApply(rolloutIds: string[]) {
    try {
      const result = await applyRollouts.mutateAsync(rolloutIds)
      const started = result.filter(
        (rollout) => rollout.status === "applying"
      ).length
      toast({
        title: "Apply started",
        description: `Applying ${started} target${started === 1 ? "" : "s"}.`,
      })
      await live.refetch()
    } catch (err) {
      toastApiError("Failed to apply rollouts", err)
    }
  }

  async function handleUnassign(target: ManagedTargetRead) {
    try {
      const rollout = await unassignTarget.mutateAsync(target.id)
      setUnassign({ rolloutId: rollout.id, target })
    } catch (err) {
      toastApiError("Failed to start unassign preview", err)
    }
  }

  return (
    <section className="space-y-4">
      <div className="flex items-end justify-between gap-4">
        <div className="space-y-1">
          <h3 className="text-lg font-semibold">Targets</h3>
          <p className="text-sm text-muted-foreground">
            Customer workspaces that receive the package. Select targets to
            preview and apply a version.
          </p>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setAssignOpen(true)}
          disabled={!hasSource || tags.length === 0}
        >
          <PlusIcon className="mr-1 size-3" />
          Assign target
        </Button>
      </div>

      {selectedTargets.length > 0 && (
        <BumpControls
          selectedTargets={selectedTargets}
          tags={tags}
          isPending={previewRollouts.isPending}
          onPreview={(tag) =>
            startPreview(
              selectedTargets.map((target) => target.id),
              tag
            )
          }
          onClearSelection={() => setSelectedIds(new Set())}
        />
      )}

      {isLoading && (
        <div className="text-sm text-muted-foreground">Loading targets...</div>
      )}
      {!isLoading && Boolean(error) && (
        <div className="text-sm text-destructive">
          {getApiErrorDetail(error) ?? "Failed to load targets."}
        </div>
      )}
      {!isLoading && !error && targets.length === 0 && (
        <div className="text-sm text-muted-foreground">
          No targets yet. Assign a customer workspace to get started.
        </div>
      )}
      {!isLoading && !error && targets.length > 0 && (
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8">
                  <Checkbox
                    aria-label="Select all targets"
                    checked={headerChecked}
                    onCheckedChange={(value) =>
                      setSelectedIds(
                        value === true
                          ? new Set(targets.map((target) => target.id))
                          : new Set()
                      )
                    }
                  />
                </TableHead>
                <TableHead className="text-xs">Organization</TableHead>
                <TableHead className="text-xs">Workspace</TableHead>
                <TableHead className="text-xs">Pinned</TableHead>
                <TableHead className="text-xs">Applied</TableHead>
                <TableHead className="text-xs">Status</TableHead>
                <TableHead className="text-xs">Last rollout</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {targets.map((target) => (
                <TableRow
                  key={target.id}
                  data-state={
                    selectedIds.has(target.id) ? "selected" : undefined
                  }
                >
                  <TableCell>
                    <Checkbox
                      aria-label={`Select ${target.workspace_name}`}
                      checked={selectedIds.has(target.id)}
                      onCheckedChange={(value) =>
                        toggleTarget(target.id, value === true)
                      }
                    />
                  </TableCell>
                  <TableCell className="text-xs">
                    {target.organization_name}
                  </TableCell>
                  <TableCell className="text-xs">
                    {target.workspace_name}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {target.pinned_tag}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {target.last_applied_tag ?? "-"}
                  </TableCell>
                  <TableCell>
                    <TargetStatusBadge status={target.last_rollout_status} />
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {target.last_rollout_at
                      ? getRelativeTime(new Date(target.last_rollout_at))
                      : "-"}
                  </TableCell>
                  <TableCell>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-8"
                          aria-label={`Actions for ${target.workspace_name}`}
                        >
                          <MoreHorizontalIcon className="size-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onSelect={() => setPinTarget(target)}>
                          Change pin
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onSelect={() => setReferencesTarget(target)}
                        >
                          References
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          className="text-destructive focus:text-destructive"
                          disabled={unassignTarget.isPending}
                          onSelect={() => handleUnassign(target)}
                        >
                          Unassign
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {rollouts.length > 0 && (
        <RolloutProgress
          rollouts={rollouts}
          targetsById={targetsById}
          isActive={live.isActive}
          applyPending={applyRollouts.isPending}
          onApply={handleApply}
          onShowDetails={setDetailRolloutId}
          onMatchReferences={setReferencesTarget}
          onDismiss={() => setTracked([])}
        />
      )}

      <AssignTargetDialog
        open={assignOpen}
        onOpenChange={setAssignOpen}
        tags={tags}
      />
      <ChangePinDialog
        target={pinTarget}
        tags={tags}
        onOpenChange={(open) => {
          if (!open) setPinTarget(null)
        }}
      />
      <ReferenceMatchDialog
        target={referencesTarget}
        onOpenChange={(open) => {
          if (!open) setReferencesTarget(null)
        }}
        onPreviewAgain={(target, tag) => startPreview([target.id], tag)}
      />
      <UnassignTargetDialog
        rolloutId={unassign?.rolloutId ?? null}
        target={unassign?.target ?? null}
        onOpenChange={(open) => {
          if (!open) setUnassign(null)
        }}
      />
      <RolloutDetailSheet
        rollout={detailRollout}
        target={
          detailRollout?.target_id
            ? (targetsById.get(detailRollout.target_id) ?? null)
            : null
        }
        onOpenChange={(open) => {
          if (!open) setDetailRolloutId(null)
        }}
      />
    </section>
  )
}
