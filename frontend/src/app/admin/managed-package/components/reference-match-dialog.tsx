"use client"

import { useState } from "react"
import type {
  ManagedReferenceKind,
  ManagedReferenceMatchItem,
  ManagedTargetRead,
} from "@/client"
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
import { toast } from "@/components/ui/use-toast"
import {
  referenceKindLabel,
  toastApiError,
  UUID_PATTERN,
} from "../managed-package-utils"
import {
  useLatestTargetPreview,
  useReferenceMatches,
  useSaveReferenceMatches,
} from "../use-managed-package"

/** Props for {@link ReferenceMatchDialog}. */
export interface ReferenceMatchDialogProps {
  target: ManagedTargetRead | null
  onOpenChange: (open: boolean) => void
  /** Start a new preview of `tag` (null means the pin) for the target. */
  onPreviewAgain: (target: ManagedTargetRead, tag: string | null) => void
}

interface ReferenceRow {
  key: string
  kind: ManagedReferenceKind
  sourceKey: string
  label: string
  unresolved: boolean
  initialValue: string
}

/** Match a target's package references to resources in its own workspace. */
export function ReferenceMatchDialog({
  target,
  onOpenChange,
  onPreviewAgain,
}: ReferenceMatchDialogProps) {
  return (
    <Dialog open={target !== null} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        {target && (
          <ReferenceMatchContent
            target={target}
            onPreviewAgain={(tag) => {
              onOpenChange(false)
              onPreviewAgain(target, tag)
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

function ReferenceMatchContent({
  target,
  onPreviewAgain,
}: {
  target: ManagedTargetRead
  onPreviewAgain: (tag: string | null) => void
}) {
  const { rollout, isLoading: previewLoading } = useLatestTargetPreview(
    target.id
  )
  const { matches, isLoading: matchesLoading } = useReferenceMatches(target.id)

  const header = (
    <DialogHeader>
      <DialogTitle>References</DialogTitle>
      <DialogDescription>
        Enter the ID of the matching resource in {target.workspace_name}. Saved
        matches are reused by later version bumps.
      </DialogDescription>
    </DialogHeader>
  )

  if (previewLoading || matchesLoading) {
    return (
      <>
        {header}
        <DialogBody>
          <div className="text-sm text-muted-foreground">Loading...</div>
        </DialogBody>
      </>
    )
  }

  const rows = buildRows(rollout?.preview?.references ?? [], matches)
  return (
    <>
      {header}
      <ReferenceMatchForm
        targetId={target.id}
        rows={rows}
        onPreviewAgain={() => onPreviewAgain(rollout?.tag ?? null)}
      />
    </>
  )
}

function buildRows(
  references: {
    kind: ManagedReferenceKind
    source_key: string
    label: string
  }[],
  matches: ManagedReferenceMatchItem[]
): ReferenceRow[] {
  const savedByKey = new Map(
    matches.map((match) => [
      `${match.reference_kind}:${match.source_key}`,
      match.resolved_id ?? "",
    ])
  )
  const rows: ReferenceRow[] = references.map((reference) => {
    const key = `${reference.kind}:${reference.source_key}`
    return {
      key,
      kind: reference.kind,
      sourceKey: reference.source_key,
      label: reference.label,
      unresolved: true,
      initialValue: savedByKey.get(key) ?? "",
    }
  })
  const seen = new Set(rows.map((row) => row.key))
  for (const match of matches) {
    const key = `${match.reference_kind}:${match.source_key}`
    if (seen.has(key)) {
      continue
    }
    rows.push({
      key,
      kind: match.reference_kind,
      sourceKey: match.source_key,
      label: match.source_key,
      unresolved: false,
      initialValue: match.resolved_id ?? "",
    })
  }
  return rows
}

function ReferenceMatchForm({
  targetId,
  rows,
  onPreviewAgain,
}: {
  targetId: string
  rows: ReferenceRow[]
  onPreviewAgain: () => void
}) {
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(rows.map((row) => [row.key, row.initialValue]))
  )
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [saved, setSaved] = useState(false)
  const saveMatches = useSaveReferenceMatches()

  async function handleSave() {
    const nextErrors: Record<string, string> = {}
    const payload: ManagedReferenceMatchItem[] = []
    for (const row of rows) {
      const value = (values[row.key] ?? "").trim()
      if (value === "") {
        // An empty secret store match means "leave it unlinked".
        if (row.kind === "secret_store") {
          payload.push({
            reference_kind: row.kind,
            source_key: row.sourceKey,
            resolved_id: null,
          })
        }
        continue
      }
      if (!UUID_PATTERN.test(value)) {
        nextErrors[row.key] = "Enter a valid UUID."
        continue
      }
      payload.push({
        reference_kind: row.kind,
        source_key: row.sourceKey,
        resolved_id: value,
      })
    }
    setErrors(nextErrors)
    if (Object.keys(nextErrors).length > 0) {
      return
    }
    try {
      await saveMatches.mutateAsync({ targetId, matches: payload })
      setSaved(true)
      toast({
        title: "References saved",
        description: "Preview again to check the target with these matches.",
      })
    } catch (err) {
      toastApiError("Failed to save references", err)
    }
  }

  return (
    <>
      <DialogBody>
        {rows.length === 0 ? (
          <div className="text-sm text-muted-foreground">
            The latest preview has no unresolved references and no matches are
            saved.
          </div>
        ) : (
          <ul className="space-y-4">
            {rows.map((row) => (
              <li key={row.key} className="space-y-1.5">
                <div className="flex items-baseline gap-2 text-sm">
                  <span className="text-muted-foreground">
                    {referenceKindLabel(row.kind)}
                  </span>
                  <span className="font-medium">{row.label}</span>
                  {!row.unresolved && (
                    <span className="text-xs text-muted-foreground">
                      (saved)
                    </span>
                  )}
                </div>
                <div className="truncate font-mono text-xs text-muted-foreground">
                  {row.sourceKey}
                </div>
                <Input
                  value={values[row.key] ?? ""}
                  onChange={(event) => {
                    const value = event.target.value
                    setValues((current) => ({ ...current, [row.key]: value }))
                    setSaved(false)
                  }}
                  placeholder={
                    row.kind === "secret_store"
                      ? "Secret store ID, or empty to leave unlinked"
                      : "Resource ID in the target workspace"
                  }
                  className="font-mono text-xs"
                />
                {errors[row.key] && (
                  <p className="text-xs text-destructive">{errors[row.key]}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </DialogBody>
      <DialogFooter>
        {saved && (
          <Button size="sm" variant="outline" onClick={onPreviewAgain}>
            Preview again
          </Button>
        )}
        <Button
          size="sm"
          onClick={handleSave}
          disabled={rows.length === 0 || saveMatches.isPending}
        >
          {saveMatches.isPending ? "Saving..." : "Save"}
        </Button>
      </DialogFooter>
    </>
  )
}
