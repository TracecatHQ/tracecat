"use client"

import { useRouter } from "next/navigation"
import { type FormEvent, useEffect, useId, useState } from "react"
import type { DetachedCopy, SyncResourceType } from "@/client"
import { Spinner } from "@/components/loading/spinner"
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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { toast } from "@/components/ui/use-toast"
import { useDetachManagedResource } from "@/hooks/use-managed-resources"
import { getApiErrorDetail } from "@/lib/errors"
import { useWorkspaceId } from "@/providers/workspace-id"

/** Resource types whose names only allow letters, digits and underscores. */
const UNDERSCORE_NAME_TYPES: ReadonlySet<SyncResourceType> = new Set([
  "table",
  "variable",
  "secret_metadata",
  "case_field",
])

/** Suggest a name for the copy, e.g. `triage` -> `triage-copy`. */
export function suggestCopyName(
  resourceType: SyncResourceType,
  currentName: string
): string {
  const separator = UNDERSCORE_NAME_TYPES.has(resourceType) ? "_" : "-"
  return `${currentName}${separator}copy`
}

/** The page that shows a detached copy, for types with their own page. */
function copyPath(workspaceId: string, copy: DetachedCopy): string | null {
  const base = `/workspaces/${workspaceId}`
  switch (copy.resource_type) {
    case "workflow":
      return `${base}/workflows/${copy.local_id}`
    case "agent_preset":
      return `${base}/agents/${copy.local_id}`
    case "skill":
      return `${base}/skills/${copy.local_id}`
    case "table":
      return `${base}/tables/${copy.local_id}`
    default:
      return null
  }
}

/** Props for {@link DetachDialog}. */
export interface DetachDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  resourceType: SyncResourceType
  /** Resource id; for `case_field`, the managed record's `local_id`. */
  localId: string
  /**
   * The resource's natural name (workflow alias, preset or skill slug,
   * otherwise its name). Prefills `<name>-copy`.
   */
  currentName: string
  /**
   * Called with the copy after a detach. Defaults to opening the copy's page
   * when it has one; list-only resources just refresh.
   */
  onDetached?: (copy: DetachedCopy) => void
}

/**
 * Ask for a name and detach a managed resource into an editable copy.
 * Name conflicts (409) and other refusals (400) show inline.
 */
export function DetachDialog({
  open,
  onOpenChange,
  resourceType,
  localId,
  currentName,
  onDetached,
}: DetachDialogProps) {
  const workspaceId = useWorkspaceId()
  const router = useRouter()
  const inputId = useId()
  const formId = useId()
  const { detachResource, detachPending, detachError, resetDetach } =
    useDetachManagedResource(workspaceId)
  const [name, setName] = useState(() =>
    suggestCopyName(resourceType, currentName)
  )

  useEffect(() => {
    if (open) {
      setName(suggestCopyName(resourceType, currentName))
      resetDetach()
    }
  }, [open, resourceType, currentName, resetDetach])

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const newName = name.trim()
    if (!newName) {
      return
    }
    let copy: DetachedCopy
    try {
      copy = await detachResource({
        resource_type: resourceType,
        local_id: localId,
        new_name: newName,
      })
    } catch {
      // Shown inline from detachError.
      return
    }
    toast({
      title: "Detached copy created",
      description: `${copy.name} is now editable.`,
    })
    onOpenChange(false)
    if (onDetached) {
      onDetached(copy)
      return
    }
    const path = copyPath(workspaceId, copy)
    if (path) {
      router.push(path)
    }
  }

  const errorMessage = detachError
    ? (getApiErrorDetail(detachError) ?? "Could not detach this resource.")
    : null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="sm">
        <DialogHeader>
          <DialogTitle>Detach to editable copy</DialogTitle>
          <DialogDescription>
            Creates a copy you own and can edit. The managed original stays
            locked and keeps receiving updates.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <form id={formId} onSubmit={handleSubmit} className="space-y-2">
            <Label htmlFor={inputId} className="text-xs">
              Name of the copy
            </Label>
            <Input
              id={inputId}
              autoFocus
              value={name}
              onChange={(event) => setName(event.target.value)}
              aria-invalid={errorMessage !== null}
            />
            {errorMessage && (
              <p className="text-xs text-destructive">{errorMessage}</p>
            )}
          </form>
        </DialogBody>
        <DialogFooter>
          <Button
            size="sm"
            type="submit"
            form={formId}
            disabled={detachPending || !name.trim()}
          >
            {detachPending && <Spinner className="mr-2 size-4" />}
            Detach
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
