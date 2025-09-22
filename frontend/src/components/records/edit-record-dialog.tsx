"use client"

import { AlertTriangle } from "lucide-react"
import { useEffect, useState } from "react"
import type { RecordRead } from "@/client"
import { ApiError } from "@/client"
import { CodeEditor } from "@/components/editor/codemirror/code-editor"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useUpdateRecord } from "@/hooks/use-records"
import { useWorkspaceId } from "@/providers/workspace-id"

interface EditRecordDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  record: RecordRead | null
}

export function EditRecordDialog({
  open,
  onOpenChange,
  record,
}: EditRecordDialogProps) {
  const workspaceId = useWorkspaceId()
  const [submissionError, setSubmissionError] = useState<string | null>(null)
  const [jsonValue, setJsonValue] = useState<string>("")
  const [isValidJson, setIsValidJson] = useState(true)
  const { updateRecord, updateRecordIsPending } = useUpdateRecord()

  useEffect(() => {
    if (record?.data) {
      setJsonValue(JSON.stringify(record.data, null, 2))
      setIsValidJson(true)
      setSubmissionError(null)
    }
  }, [record])

  const handleJsonChange = (value: string) => {
    setJsonValue(value)
    try {
      JSON.parse(value)
      setIsValidJson(true)
      setSubmissionError(null)
    } catch {
      setIsValidJson(false)
    }
  }

  const handleSubmit = async () => {
    if (!record || !isValidJson) return

    try {
      const parsedData = JSON.parse(jsonValue)
      await updateRecord({
        workspaceId,
        entityId: record.entity_id,
        recordId: record.id,
        data: parsedData,
      })
      onOpenChange(false)
    } catch (error) {
      let detail: string | undefined
      if (error instanceof ApiError) {
        const body: unknown = error.body
        if (body && typeof body === "object" && "detail" in body) {
          const d = (body as { detail?: unknown }).detail
          if (typeof d === "string") {
            detail = d
          }
        }
      }
      const errorMessage =
        detail ||
        (error instanceof Error && error.message
          ? error.message
          : "Failed to update record. Please try again.")
      setSubmissionError(errorMessage)
    }
  }

  const handleOpenChange = (newOpen: boolean) => {
    if (!newOpen) {
      setSubmissionError(null)
      setJsonValue("")
      setIsValidJson(true)
    }
    onOpenChange(newOpen)
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-3xl max-h-[80vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle>Edit record</DialogTitle>
          <DialogDescription>
            Modify the record data in JSON format.
          </DialogDescription>
        </DialogHeader>
        {submissionError && (
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>Error updating record</AlertTitle>
            <AlertDescription>{submissionError}</AlertDescription>
          </Alert>
        )}
        <div className="flex-1 overflow-auto min-h-[300px]">
          <CodeEditor
            value={jsonValue}
            onChange={handleJsonChange}
            language="json"
            className="h-full"
          />
        </div>
        {!isValidJson && (
          <div className="text-sm text-destructive">
            Invalid JSON format. Please check your syntax.
          </div>
        )}
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => handleOpenChange(false)}
            disabled={updateRecordIsPending}
          >
            Cancel
          </Button>
          <Button
            type="button"
            onClick={handleSubmit}
            disabled={!isValidJson || updateRecordIsPending}
          >
            {updateRecordIsPending ? "Saving..." : "Save changes"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
