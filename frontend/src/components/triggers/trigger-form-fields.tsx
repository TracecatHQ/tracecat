"use client"

import { useEffect, useRef } from "react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useTriggeredWorkflowIds } from "@/hooks/use-triggers"
import { useWorkflowManager } from "@/lib/hooks"
import { workflowUuidToShortId } from "@/lib/workflow-ids"
import { useWorkspaceId } from "@/providers/workspace-id"

/**
 * The workflow picker of a new webhook or case trigger. A workflow whose
 * webhook (or case trigger) is already a trigger is shown as such and can't
 * be picked: the create would be refused.
 */
export function TriggerWorkflowSelect({
  kind,
  value,
  onValueChange,
  disabled,
}: {
  kind: "webhook" | "case_trigger"
  value: string
  onValueChange: (workflowId: string) => void
  disabled: boolean
}) {
  const workspaceId = useWorkspaceId()
  const canReadWorkflows = useScopeCheck("workflow:read")
  const { workflows, workflowsLoading } = useWorkflowManager(undefined, {
    listEnabled: canReadWorkflows === true,
  })
  const triggered = useTriggeredWorkflowIds(workspaceId, kind)

  const listed = workflows ?? []
  let placeholder = "Select a workflow"
  if (workflowsLoading) {
    placeholder = "Loading workflows…"
  } else if (canReadWorkflows !== true) {
    placeholder = "You can't list workflows"
  } else if (listed.length === 0) {
    placeholder = "No workflows yet"
  }

  return (
    <Select
      value={value}
      onValueChange={onValueChange}
      disabled={disabled || workflowsLoading}
    >
      <SelectTrigger className="text-xs" aria-label="Workflow">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {listed.map((workflow) => {
          const isTrigger = triggered.has(workflowUuidToShortId(workflow.id))
          return (
            <SelectItem
              key={workflow.id}
              value={workflow.id}
              disabled={isTrigger}
              className="text-xs"
            >
              {workflow.title}
              {isTrigger ? (
                <span className="ml-2 text-muted-foreground">
                  Already a trigger
                </span>
              ) : null}
            </SelectItem>
          )
        })}
      </SelectContent>
    </Select>
  )
}

/**
 * A trigger form's error line. It scrolls itself into view when it appears
 * or changes, so a refused Create is visible without scrolling the dialog.
 */
export function TriggerFormError({ message }: { message: string | null }) {
  const ref = useRef<HTMLParagraphElement>(null)

  useEffect(() => {
    if (message) {
      ref.current?.scrollIntoView?.({ block: "nearest" })
    }
  }, [message])

  if (!message) {
    return null
  }
  return (
    <p
      ref={ref}
      role="alert"
      className="whitespace-pre-line text-xs text-destructive"
    >
      {message}
    </p>
  )
}
