"use client"

import { formatDistanceToNow } from "date-fns"
import { Calendar } from "lucide-react"
import { useState } from "react"
import type { RunbookExecuteRequest, RunbookRead } from "@/client"
import { CaseCommentViewer } from "@/components/cases/case-description-editor"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useToast } from "@/components/ui/use-toast"
import { useRunRunbook } from "@/hooks/use-runbook"
import { capitalizeFirst } from "@/lib/utils"

interface RunbookExecuteDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  runbook: RunbookRead | null
  workspaceId: string
  entityType: "case"
  entityId: string
}

export function RunbookExecuteDialog({
  open,
  onOpenChange,
  runbook,
  workspaceId,
  entityType,
  entityId,
}: RunbookExecuteDialogProps) {
  const [isExecuting, setIsExecuting] = useState(false)
  const { toast } = useToast()
  const { runRunbook } = useRunRunbook(workspaceId)

  const handleExecute = async () => {
    if (!runbook) return

    setIsExecuting(true)
    try {
      const request: RunbookExecuteRequest = {
        entities: [
          {
            entity_id: entityId,
            entity_type: entityType,
          },
        ],
      }

      await runRunbook({
        runbookId: runbook.id,
        request,
      })

      toast({
        title: "Runbook execution started",
        description: "The runbook is being executed on this case.",
        action: (
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              // Force a page refresh to show the new chat
              // This is a simple solution that ensures the chat list is refreshed
              window.location.reload()
            }}
          >
            Go to run
          </Button>
        ),
      })

      onOpenChange(false)
    } catch (error) {
      console.error("Failed to execute runbook:", error)
      toast({
        title: "Failed to execute runbook",
        description:
          error instanceof Error ? error.message : "An error occurred",
        variant: "destructive",
      })
    } finally {
      setIsExecuting(false)
    }
  }

  if (!runbook) return null

  const source = runbook.meta?.case_slug
    ? `Created from ${runbook.meta.case_slug}`
    : runbook.meta?.chat_id
      ? `Created from chat ${runbook.meta.chat_id}`
      : `Created directly`

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader className="space-y-2">
          <DialogTitle>{runbook.title}</DialogTitle>
          <div className="flex items-center gap-4 text-xs text-muted-foreground">
            <div className="flex items-center gap-1">
              <Calendar className="h-3 w-3" />
              <span>
                {capitalizeFirst(
                  formatDistanceToNow(new Date(runbook.created_at), {
                    addSuffix: true,
                  })
                )}
              </span>
            </div>
            <span>•</span>
            <span>{source}</span>
          </div>
        </DialogHeader>

        <div className="space-y-4">
          {runbook.summary && (
            <div className="min-h-[400px] max-h-[500px] overflow-y-auto">
              <CaseCommentViewer
                content={runbook.summary}
                className="text-sm"
              />
            </div>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isExecuting}
          >
            Cancel
          </Button>
          <Button onClick={handleExecute} disabled={isExecuting}>
            {isExecuting ? "Executing..." : "Execute"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
