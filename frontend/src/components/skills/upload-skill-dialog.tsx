"use client"

import { Loader2, Upload } from "lucide-react"
import { type ChangeEvent, type DragEvent, useRef } from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"

type UploadSkillDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  isDragOver: boolean
  onDragOver: (event: DragEvent<HTMLDivElement>) => void
  onDragLeave: () => void
  onDrop: (event: DragEvent<HTMLDivElement>) => void
  onDirectoryInput: (event: ChangeEvent<HTMLInputElement>) => void
  uploadSkillPending: boolean
}

/**
 * Modal for uploading a skill directory from disk via drag-drop or file
 * picker. Mirrors the workflow import dialog pattern.
 */
export function UploadSkillDialog({
  open,
  onOpenChange,
  isDragOver,
  onDragOver,
  onDragLeave,
  onDrop,
  onDirectoryInput,
  uploadSkillPending,
}: UploadSkillDialogProps) {
  const directoryInputRef = useRef<HTMLInputElement>(null)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>Upload skill</DialogTitle>
          <DialogDescription>
            Upload an existing skill directory containing a SKILL.md file and
            any bundled resources.
          </DialogDescription>
        </DialogHeader>

        <div
          className={cn(
            "flex flex-col items-center justify-center gap-3 rounded-md border border-dashed px-6 py-10 text-center transition-colors",
            isDragOver
              ? "border-foreground bg-muted/60"
              : "border-muted-foreground/25"
          )}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={(event) => void onDrop(event)}
        >
          <Upload className="size-6 text-muted-foreground" />
          <div className="space-y-1">
            <div className="text-sm font-medium">
              Drop a skill directory here
            </div>
            <p className="text-xs text-muted-foreground">
              Or pick a folder from your computer.
            </p>
          </div>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            disabled={uploadSkillPending}
            onClick={() => directoryInputRef.current?.click()}
          >
            {uploadSkillPending ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : (
              <Upload className="mr-2 size-4" />
            )}
            Choose folder
          </Button>
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={uploadSkillPending}
          >
            Cancel
          </Button>
        </DialogFooter>

        <input
          ref={directoryInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(event) => void onDirectoryInput(event)}
          {...({
            directory: "",
            webkitdirectory: "",
          } as Record<string, string>)}
        />
      </DialogContent>
    </Dialog>
  )
}
