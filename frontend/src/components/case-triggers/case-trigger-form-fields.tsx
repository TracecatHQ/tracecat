"use client"

import { useMemo } from "react"
import type { CaseEventType } from "@/client"
import { CASE_EVENT_SUGGESTIONS } from "@/components/builder/panel/case-event-suggestions"
import { MultiTagCommandInput } from "@/components/tags-input"
import { Label } from "@/components/ui/label"
import { useCaseTagCatalog } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

/**
 * The case events that start a workflow, as on the workflow page's Case
 * triggers panel and in the Triggers tab.
 */
export function CaseEventsField({
  value,
  onChange,
  disabled,
}: {
  value: CaseEventType[]
  onChange: (value: CaseEventType[]) => void
  disabled?: boolean
}) {
  return (
    <div className="space-y-2">
      <Label className="text-xs">Case events</Label>
      <MultiTagCommandInput
        value={value}
        onChange={(values) => onChange(values as CaseEventType[])}
        suggestions={CASE_EVENT_SUGGESTIONS}
        searchKeys={["label", "value", "group"]}
        placeholder="Select case events..."
        disabled={disabled}
        allowCustomTags={false}
      />
      <p className="text-xs text-muted-foreground">
        Choose which case events will fire this workflow.
      </p>
    </div>
  )
}

function tagPlaceholder(loading: boolean, error: unknown): string {
  if (loading) {
    return "Loading tags..."
  }
  if (error) {
    // Listing tags needs case:read, which changing a case trigger does not.
    return "Couldn't load case tags"
  }
  return "Select case tags..."
}

/**
 * The case tags an event's case must carry to start the workflow. Suggests
 * the workspace's case tags; empty applies no tag filter.
 */
export function CaseTagFiltersField({
  value,
  onChange,
  disabled,
}: {
  value: string[]
  onChange: (value: string[]) => void
  disabled?: boolean
}) {
  const workspaceId = useWorkspaceId()
  const { caseTags, caseTagsIsLoading, caseTagsError } =
    useCaseTagCatalog(workspaceId)
  const suggestions = useMemo(
    () =>
      (caseTags ?? []).map((tag) => ({
        id: tag.id,
        label: tag.name,
        value: tag.ref,
        description: tag.ref,
      })),
    [caseTags]
  )
  return (
    <div className="space-y-2">
      <Label className="text-xs">Tag allowlist</Label>
      <MultiTagCommandInput
        value={value}
        onChange={onChange}
        suggestions={suggestions}
        searchKeys={["label", "value", "description"]}
        placeholder={tagPlaceholder(caseTagsIsLoading, caseTagsError)}
        disabled={disabled || caseTagsIsLoading}
        allowCustomTags={false}
      />
      <p className="text-xs text-muted-foreground">
        If empty, no tag filtering is applied.
      </p>
      <p className="text-xs text-muted-foreground">
        Tag events match the added or removed tag; other events match current
        case tags. Workflow-originated events are ignored.
      </p>
    </div>
  )
}
