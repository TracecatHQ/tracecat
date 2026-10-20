"use client"

import { useState } from "react"
import {
  type CaseDropdownDefinitionRead,
  type CaseQuery,
  type CaseTagRead,
  casesListCaseFilterFields,
  type Filter,
  type WorkspaceMember,
} from "@/client"
import { $CaseFilterMetadata } from "@/client/schemas.gen"
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
import { useQuery } from "@/lib/query"
import { useWorkspaceId } from "@/providers/workspace-id"
import { FilterNode } from "./case-filter-node"
import { CaseFilterSummary } from "./case-filter-summary"
import { conditionCount, newCondition } from "./case-filter-utils"

/** Edit the generated server filter tree, keeping drafts separate from applied queries. */
export function CaseFilterEditor({
  value,
  onChange,
  members,
  tags,
  dropdowns,
}: {
  value: CaseQuery["filters"]
  onChange: (value: CaseQuery["filters"]) => void
  members?: WorkspaceMember[]
  tags?: CaseTagRead[]
  dropdowns?: CaseDropdownDefinitionRead[]
}) {
  const workspaceId = useWorkspaceId()
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<Filter | null>(null)
  const metadata = useQuery({
    queryKey: ["cases", "filter-fields", workspaceId],
    queryFn: () => casesListCaseFilterFields({ workspaceId }),
    enabled: open || Boolean(value),
  })
  const choiceLabels: Record<string, Record<string, string>> = {
    assignee_id: Object.fromEntries(
      (members ?? []).map((member) => [member.user_id, member.email])
    ),
    tags: Object.fromEntries((tags ?? []).map((tag) => [tag.ref, tag.name])),
    ...Object.fromEntries(
      (dropdowns ?? []).map((dropdown) => [
        `dropdowns.${dropdown.ref}`,
        Object.fromEntries(
          (dropdown.options ?? []).map((option) => [option.ref, option.label])
        ),
      ])
    ),
  }
  const fields = (metadata.data?.fields ?? []).map((field) => {
    if (field.field === "assignee_id")
      return { ...field, choices: members?.map((member) => member.user_id) }
    if (field.field === "tags")
      return { ...field, choices: tags?.map((tag) => tag.ref) }
    const dropdown = dropdowns?.find(
      (item) => `dropdowns.${item.ref}` === field.field
    )
    if (dropdown)
      return {
        ...field,
        label: dropdown.name,
        choices: (dropdown.options ?? []).map((option) => option.ref),
      }
    return field
  })
  return (
    <>
      {value && (
        <CaseFilterSummary node={value} onChange={onChange} fields={fields} />
      )}
      <Button
        variant="outline"
        size="sm"
        className="h-6 text-xs"
        onClick={() => {
          setDraft(value ?? { and: [newCondition()] })
          setOpen(true)
        }}
      >
        Conditions{value ? ` (${conditionCount(value)})` : ""}
      </Button>
      {value && (
        <Button
          variant="ghost"
          size="sm"
          className="h-6 text-xs"
          onClick={() => onChange(null)}
        >
          Clear conditions
        </Button>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent size="xl">
          <DialogHeader>
            <DialogTitle>Case conditions</DialogTitle>
            <DialogDescription>
              Match all or any conditions. These also combine with the quick
              filters.
            </DialogDescription>
          </DialogHeader>
          <form
            id="case-conditions"
            className="contents"
            onSubmit={(event) => {
              event.preventDefault()
              onChange(draft)
              setOpen(false)
            }}
          >
            <DialogBody className="space-y-4">
              <p className="text-xs text-muted-foreground">
                Unset scalar fields do not match comparisons or exclusions.
                Select “Include unset” to also match missing values. Payload
                equality distinguishes text, numbers, and booleans. Use
                dot-separated keys, for example
                payload.metadata.product.vendor_name. For payloads, unset means
                a missing path; an explicit JSON null is present.
              </p>
              {metadata.isLoading && <p className="text-xs">Loading fields…</p>}
              {metadata.error && (
                <p role="alert" className="text-xs text-destructive">
                  Could not load filter fields. Reopen to retry.
                </p>
              )}
              {draft && metadata.data && (
                <FilterNode
                  node={draft}
                  onChange={setDraft}
                  onRemove={() => setDraft(null)}
                  fields={fields}
                  choiceLabels={choiceLabels}
                  depth={1}
                  maxDepth={
                    metadata.data.max_depth ??
                    $CaseFilterMetadata.properties.max_depth.default
                  }
                  canAdd={
                    conditionCount(draft) <
                    (metadata.data.max_conditions ??
                      $CaseFilterMetadata.properties.max_conditions.default)
                  }
                />
              )}
              {!draft && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setDraft({ and: [newCondition()] })}
                >
                  Add conditions
                </Button>
              )}
            </DialogBody>
            <DialogFooter>
              <Button type="submit" size="sm" disabled={!metadata.data}>
                Apply conditions
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  )
}
