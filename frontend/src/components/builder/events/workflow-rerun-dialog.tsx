"use client"

import { AlertTriangleIcon, LoaderIcon } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import type { WorkflowRerunActionMatch } from "@/client"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useWorkflowDefinitions } from "@/hooks/use-workflow-definitions"
import {
  useRerunWorkflowExecution,
  useWorkflowRerunPreview,
} from "@/hooks/use-workflow-rerun"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkspaceId } from "@/providers/workspace-id"

const DRAFT_TARGET = "draft"

/** Rerun a past execution against the draft or a published version. */
export function WorkflowRerunDialog({
  executionId,
  workflowId,
  open,
  onOpenChange,
}: {
  executionId: string
  workflowId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Rerun with fixed results</DialogTitle>
          <DialogDescription>
            Reuse results of actions that succeeded in this run. Everything else
            runs again.
          </DialogDescription>
        </DialogHeader>
        {open && (
          <WorkflowRerunDialogContent
            executionId={executionId}
            workflowId={workflowId}
            onDone={() => onOpenChange(false)}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

function WorkflowRerunDialogContent({
  executionId,
  workflowId,
  onDone,
}: {
  executionId: string
  workflowId: string
  onDone: () => void
}) {
  const workspaceId = useWorkspaceId()
  const { setCurrentExecutionId, expandSidebarAndFocusEvents } =
    useWorkflowBuilder()
  const [target, setTarget] = useState<string>(DRAFT_TARGET)
  const useDraft = target === DRAFT_TARGET
  const version = useDraft ? null : Number(target)

  const { definitions } = useWorkflowDefinitions(workspaceId, workflowId)
  const { rerunPlan, rerunPlanIsLoading, rerunPlanError } =
    useWorkflowRerunPreview(workspaceId, executionId, {
      use_draft: useDraft,
      version,
    })
  const { rerunExecution, rerunExecutionIsPending } = useRerunWorkflowExecution(
    workspaceId,
    executionId
  )

  const [selected, setSelected] = useState<Set<string>>(new Set())
  useEffect(() => {
    setSelected(new Set(rerunPlan?.fixed_action_refs ?? []))
  }, [rerunPlan])

  const fixable = useMemo(
    () => new Set(rerunPlan?.fixed_action_refs ?? []),
    [rerunPlan]
  )

  async function handleRun() {
    const result = await rerunExecution({
      use_draft: useDraft,
      version,
      fixed_action_refs: [...selected].sort(),
    })
    setCurrentExecutionId(result.wf_exec_id)
    expandSidebarAndFocusEvents()
    onDone()
  }

  return (
    <>
      <DialogBody>
        <div className="flex flex-col gap-4">
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm">Target</span>
            <Select value={target} onValueChange={setTarget}>
              <SelectTrigger className="h-8 w-56 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={DRAFT_TARGET} className="text-xs">
                  Draft
                </SelectItem>
                {definitions?.map((defn) => (
                  <SelectItem
                    key={defn.version}
                    value={String(defn.version)}
                    className="text-xs"
                  >
                    Version {defn.version}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {rerunPlanIsLoading && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <LoaderIcon className="size-3 animate-spin" />
              <span>Matching actions...</span>
            </div>
          )}
          {rerunPlanError && (
            <Alert variant="destructive">
              <AlertTitle>Could not match this run</AlertTitle>
              <AlertDescription>
                {getErrorMessage(rerunPlanError.body)}
              </AlertDescription>
            </Alert>
          )}
          {rerunPlan?.inputs_error && (
            <Alert variant="destructive">
              <AlertTitle>Inputs don&apos;t match the target</AlertTitle>
              <AlertDescription className="whitespace-pre-wrap">
                {rerunPlan.inputs_error}
              </AlertDescription>
            </Alert>
          )}
          {rerunPlan && rerunPlan.unmatched_refs.length > 0 && (
            <Alert>
              <AlertTriangleIcon className="size-4" />
              <AlertTitle>
                {rerunPlan.unmatched_refs.length} result(s) can&apos;t be reused
              </AlertTitle>
              <AlertDescription>
                The target has no matching action for{" "}
                <span className="font-mono">
                  {rerunPlan.unmatched_refs.join(", ")}
                </span>
                .
              </AlertDescription>
            </Alert>
          )}
          {rerunPlan && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8" />
                  <TableHead className="text-xs">Action</TableHead>
                  <TableHead className="text-xs">Rerun</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rerunPlan.actions.map((action) => (
                  <TableRow key={action.ref}>
                    <TableCell>
                      {fixable.has(action.ref) && (
                        <Checkbox
                          aria-label={`Fix ${action.ref}`}
                          checked={selected.has(action.ref)}
                          onCheckedChange={(checked) => {
                            setSelected((prev) => {
                              const next = new Set(prev)
                              if (checked === true) {
                                next.add(action.ref)
                              } else {
                                next.delete(action.ref)
                              }
                              return next
                            })
                          }}
                        />
                      )}
                    </TableCell>
                    <TableCell className="text-xs">
                      <div className="font-mono">{action.ref}</div>
                      <div className="text-muted-foreground">
                        {action.target_action ?? action.source_action}
                      </div>
                    </TableCell>
                    <TableCell className="text-xs">
                      <RerunStatus
                        action={action}
                        fixed={selected.has(action.ref)}
                      />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      </DialogBody>
      <DialogFooter>
        <Button
          size="sm"
          onClick={handleRun}
          disabled={
            !rerunPlan ||
            Boolean(rerunPlan.inputs_error) ||
            rerunExecutionIsPending
          }
        >
          {selected.size > 0 ? `Rerun with ${selected.size} fixed` : "Rerun"}
        </Button>
      </DialogFooter>
    </>
  )
}

function RerunStatus({
  action,
  fixed,
}: {
  action: WorkflowRerunActionMatch
  fixed: boolean
}) {
  switch (action.status) {
    case "fixed":
    case "excluded":
      return fixed ? (
        <Badge variant="secondary">Fixed</Badge>
      ) : (
        <span className="text-muted-foreground">Runs</span>
      )
    case "not_in_target":
      return <Badge variant="outline">Not in target</Badge>
    case "action_changed":
      return (
        <Badge variant="outline">
          Action changed to {action.target_action}
        </Badge>
      )
    default:
      return (
        <span className="text-muted-foreground">
          Runs{action.reason ? ` (${action.reason})` : ""}
        </span>
      )
  }
}

function getErrorMessage(body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = body.detail
    if (typeof detail === "string") {
      return detail
    }
    if (
      detail &&
      typeof detail === "object" &&
      "message" in detail &&
      typeof detail.message === "string"
    ) {
      return detail.message
    }
  }
  return "Unexpected error"
}
