"use client"

import { useEffect, useMemo, useState } from "react"
import type {
  WorkflowExecutionResetPointRead,
  WorkflowExecutionResetReapplyType,
} from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

interface ResetWorkflowRunDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  executionCount: number
  resetPoints: WorkflowExecutionResetPointRead[]
  resetPointsLoading: boolean
  isSubmitting: boolean
  onSubmit: (input: {
    eventId?: number | null
    reason?: string | null
    reapplyType: WorkflowExecutionResetReapplyType
  }) => Promise<void>
}

export function ResetWorkflowRunDialog({
  open,
  onOpenChange,
  executionCount,
  resetPoints,
  resetPointsLoading,
  isSubmitting,
  onSubmit,
}: ResetWorkflowRunDialogProps) {
  const [eventIdText, setEventIdText] = useState("")
  const [selectedPoint, setSelectedPoint] = useState("start")
  const [reason, setReason] = useState("")
  const [reapplyType, setReapplyType] =
    useState<WorkflowExecutionResetReapplyType>("all_eligible")

  useEffect(() => {
    if (!open) {
      setEventIdText("")
      setSelectedPoint("start")
      setReason("")
      setReapplyType("all_eligible")
    }
  }, [open])

  const resettablePoints = useMemo(
    () => resetPoints.filter((point) => point.is_resettable),
    [resetPoints]
  )

  const useResetPointSelect =
    executionCount === 1 && resettablePoints.length > 0

  const helperText = useMemo(() => {
    if (useResetPointSelect) {
      return "Select start or a specific reset point from workflow history."
    }
    if (!eventIdText.trim()) {
      return "Leave empty to reset from start."
    }
    return "The reset will use the nearest resettable point at or before this event."
  }, [eventIdText, useResetPointSelect])

  const handleSubmit = async () => {
    let parsedEventId: number | null = null
    if (useResetPointSelect) {
      if (selectedPoint !== "start") {
        const eventId = Number(selectedPoint)
        if (!Number.isInteger(eventId) || eventId <= 0) {
          return
        }
        parsedEventId = eventId
      }
    } else {
      const trimmedEventId = eventIdText.trim()
      if (trimmedEventId) {
        const candidateEventId = Number(trimmedEventId)
        if (!Number.isInteger(candidateEventId) || candidateEventId <= 0) {
          return
        }
        parsedEventId = candidateEventId
      }
    }

    await onSubmit({
      eventId: parsedEventId,
      reason: reason.trim() || null,
      reapplyType,
    })
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>Reset workflow run</DialogTitle>
          <DialogDescription>
            {executionCount === 1
              ? "Reset the selected workflow run from start or a specific event."
              : `Reset ${executionCount} selected workflow runs from start or a specific event.`}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="event-id">
              {useResetPointSelect ? "Reset point" : "Event ID"}
            </Label>
            {useResetPointSelect ? (
              <Select value={selectedPoint} onValueChange={setSelectedPoint}>
                <SelectTrigger id="event-id">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="start">Start</SelectItem>
                  {resettablePoints.map((point) => (
                    <SelectItem
                      key={point.event_id}
                      value={String(point.event_id)}
                    >
                      Event {point.event_id} -{" "}
                      {point.event_type.replaceAll("_", " ").toLowerCase()}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <Input
                id="event-id"
                type="number"
                min={1}
                placeholder="Start"
                value={eventIdText}
                onChange={(event) => setEventIdText(event.target.value)}
              />
            )}
            <p className="text-xs text-muted-foreground">{helperText}</p>
            {resetPointsLoading ? (
              <p className="text-xs text-muted-foreground">
                Loading reset points...
              </p>
            ) : null}
          </div>
          <div className="space-y-2">
            <Label htmlFor="reapply-type">Reapply type</Label>
            <Select
              value={reapplyType}
              onValueChange={(value: WorkflowExecutionResetReapplyType) =>
                setReapplyType(value)
              }
            >
              <SelectTrigger id="reapply-type">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all_eligible">All eligible</SelectItem>
                <SelectItem value="signal_only">Signals only</SelectItem>
                <SelectItem value="none">None</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="reason">Reason</Label>
            <Input
              id="reason"
              placeholder="Optional reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isSubmitting}
          >
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={isSubmitting}>
            Reset
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
