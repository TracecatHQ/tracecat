"use client"

import { EyeOffIcon, TriangleAlertIcon } from "lucide-react"
import { type ReactNode, useMemo } from "react"
import type { FormRead, FormResolved } from "@/client"
import { FormRenderer } from "@/components/forms/form-renderer"
import { Badge } from "@/components/ui/badge"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import type { FormConfigCheck } from "@/hooks/use-form-config"
import { cn } from "@/lib/utils"

/** Props of {@link FormPreviewPane}. */
export interface FormPreviewPaneProps {
  form: FormRead
  /** Result of checking the editor's text. Holds the preview to show. */
  check: FormConfigCheck
  /** Whether the editor holds unsaved edits. */
  isDirty: boolean
  /** Whether the caller holds `form:update`. The check needs it. */
  canEdit: boolean
}

function PreviewMessage({
  icon,
  title,
  description,
}: {
  icon: ReactNode
  title: string
  description: string
}) {
  return (
    <Empty className="py-12">
      <EmptyHeader>
        <EmptyMedia variant="icon">{icon}</EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        <EmptyDescription>{description}</EmptyDescription>
      </EmptyHeader>
    </Empty>
  )
}

/**
 * A read-only preview of the config in the editor, as a requester would
 * see it. Submitting it validates and confirms without sending anything.
 *
 * While the config has errors, the last good preview stays on screen,
 * dimmed, so the author keeps their bearings while they type.
 */
export function FormPreviewPane({
  form,
  check,
  isDirty,
  canEdit,
}: FormPreviewPaneProps) {
  const preview = check.preview
  // The validate route returns no IDs, and the renderer's `user` and `file`
  // widgets need the form's.
  const resolved = useMemo<FormResolved | null>(
    () =>
      preview && {
        ...preview,
        id: form.id,
        workspace_id: form.workspace_id,
        workflow_id: form.workflow_id,
        status: form.status,
      },
    [preview, form.id, form.workspace_id, form.workflow_id, form.status]
  )
  const isStale = check.hasErrors || check.checkFailed

  let body: ReactNode
  if (!canEdit) {
    body = (
      <PreviewMessage
        icon={<EyeOffIcon className="size-5" />}
        title="No preview"
        description="The preview is available to people who can edit forms. Open the form to see it as a requester."
      />
    )
  } else if (resolved) {
    body = (
      <div
        className={cn("transition-opacity", isStale && "opacity-50")}
        data-stale={isStale}
      >
        <FormRenderer
          formId={form.id}
          form={resolved}
          mode="preview"
          className="p-5 sm:p-5"
        />
      </div>
    )
  } else if (isStale) {
    body = (
      <PreviewMessage
        icon={<TriangleAlertIcon className="size-5" />}
        title="No preview yet"
        description={
          check.checkFailed
            ? "The config could not be checked. Edit it to try again."
            : "Fix the problems in the config to see the form."
        }
      />
    )
  } else {
    body = null
  }

  return (
    <section aria-label="Preview" className="flex h-full min-h-0 flex-col">
      <header className="flex h-9 flex-none items-center gap-2 border-b px-3 text-xs font-medium">
        <span>Preview</span>
        <span className="flex-1" />
        {canEdit && resolved && isStale && (
          <span className="truncate font-normal text-muted-foreground">
            {check.checkFailed
              ? "Couldn't check the config"
              : "Showing the last valid config"}
          </span>
        )}
        {isDirty && (
          <Badge
            variant="secondary"
            className="h-5 flex-none px-2 text-[10px] font-normal"
          >
            Unsaved changes
          </Badge>
        )}
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto bg-muted/30 p-4">
        {body}
      </div>
    </section>
  )
}
