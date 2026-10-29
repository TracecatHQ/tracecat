"use client"

import { useState } from "react"
import type { GitTagInfo } from "@/client"
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
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { toast } from "@/components/ui/use-toast"
import { useAdminOrganizations } from "@/hooks/use-admin"
import { toastApiError } from "../managed-package-utils"
import { useAssignTarget, useTargetCandidates } from "../use-managed-package"

/** Props for {@link AssignTargetDialog}. */
export interface AssignTargetDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  tags: GitTagInfo[]
}

/** Assign a customer workspace as a target pinned to a tag. */
export function AssignTargetDialog({
  open,
  onOpenChange,
  tags,
}: AssignTargetDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="md">
        {open && (
          <AssignTargetForm tags={tags} onDone={() => onOpenChange(false)} />
        )}
      </DialogContent>
    </Dialog>
  )
}

function AssignTargetForm({
  tags,
  onDone,
}: {
  tags: GitTagInfo[]
  onDone: () => void
}) {
  const [organizationId, setOrganizationId] = useState<string | null>(null)
  const [workspaceId, setWorkspaceId] = useState<string | null>(null)
  const [pinnedTag, setPinnedTag] = useState<string | null>(
    tags[0]?.name ?? null
  )
  const { organizations, isLoading: organizationsLoading } =
    useAdminOrganizations()
  const { candidates, isLoading: candidatesLoading } =
    useTargetCandidates(organizationId)
  const assignTarget = useAssignTarget()

  const available = candidates.filter(
    (candidate) => !candidate.is_source && !candidate.target_id
  )

  async function handleAssign() {
    if (!workspaceId || !pinnedTag) {
      return
    }
    try {
      const target = await assignTarget.mutateAsync({ workspaceId, pinnedTag })
      toast({
        title: "Target assigned",
        description: `${target.workspace_name} is pinned to ${target.pinned_tag}. Preview and apply to roll it out.`,
      })
      onDone()
    } catch (err) {
      toastApiError("Failed to assign target", err)
    }
  }

  let workspacePlaceholder = "Select a workspace"
  if (!organizationId) {
    workspacePlaceholder = "Select an organization first"
  } else if (candidatesLoading) {
    workspacePlaceholder = "Loading workspaces..."
  } else if (available.length === 0) {
    workspacePlaceholder = "No available workspaces"
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>Assign target</DialogTitle>
        <DialogDescription>
          Pin a customer workspace to a release. Nothing is applied until you
          preview and apply a rollout.
        </DialogDescription>
      </DialogHeader>
      <DialogBody>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label>Organization</Label>
            <Select
              value={organizationId ?? undefined}
              onValueChange={(value) => {
                setOrganizationId(value)
                setWorkspaceId(null)
              }}
              disabled={organizationsLoading}
            >
              <SelectTrigger>
                <SelectValue
                  placeholder={
                    organizationsLoading
                      ? "Loading organizations..."
                      : "Select an organization"
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {(organizations ?? []).map((organization) => (
                  <SelectItem key={organization.id} value={organization.id}>
                    {organization.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label>Workspace</Label>
            <Select
              value={workspaceId ?? undefined}
              onValueChange={setWorkspaceId}
              disabled={!organizationId || available.length === 0}
            >
              <SelectTrigger>
                <SelectValue placeholder={workspacePlaceholder} />
              </SelectTrigger>
              <SelectContent>
                {available.map((candidate) => (
                  <SelectItem
                    key={candidate.workspace_id}
                    value={candidate.workspace_id}
                  >
                    {candidate.workspace_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              The source workspace and existing targets are hidden.
            </p>
          </div>
          <div className="space-y-2">
            <Label>Pinned tag</Label>
            <Select
              value={pinnedTag ?? undefined}
              onValueChange={setPinnedTag}
              disabled={tags.length === 0}
            >
              <SelectTrigger>
                <SelectValue
                  placeholder={
                    tags.length === 0 ? "No tags yet" : "Select a tag"
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {tags.map((tag) => (
                  <SelectItem key={tag.name} value={tag.name}>
                    <span className="font-mono">{tag.name}</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      </DialogBody>
      <DialogFooter>
        <Button
          size="sm"
          onClick={handleAssign}
          disabled={!workspaceId || !pinnedTag || assignTarget.isPending}
        >
          {assignTarget.isPending ? "Assigning..." : "Assign"}
        </Button>
      </DialogFooter>
    </>
  )
}
