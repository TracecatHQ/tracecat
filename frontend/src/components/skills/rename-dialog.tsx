"use client"

import { type FormEvent, useEffect, useState } from "react"
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

type RenameDialogProps = {
  target: { fromPath: string; isFolder: boolean } | null
  error: string | null
  onCancel: () => void
  onSubmit: (newPath: string) => void | Promise<void>
}

/**
 * Dialog for renaming or relocating a skill draft file or folder.
 *
 * The user edits the full relative path; on submit the parent hook issues
 * one or more `move_file` operations against the draft.
 */
export function RenameDialog({
  target,
  error,
  onCancel,
  onSubmit,
}: RenameDialogProps) {
  const [value, setValue] = useState("")

  useEffect(() => {
    if (target) {
      setValue(target.fromPath)
    }
  }, [target])

  if (!target) {
    return null
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    void onSubmit(value)
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) {
          onCancel()
        }
      }}
    >
      <DialogContent>
        <form onSubmit={handleSubmit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>
              Rename {target.isFolder ? "folder" : "file"}
            </DialogTitle>
            <DialogDescription>
              Update the relative path. Folders will be renamed by moving every
              file inside them.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="rename-path">Relative path</Label>
            <Input
              id="rename-path"
              autoFocus
              value={value}
              onChange={(event) => setValue(event.target.value)}
            />
            {error ? (
              <p className="text-xs font-light text-destructive">{error}</p>
            ) : null}
          </div>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={!value.trim() || value.trim() === target.fromPath}
            >
              Rename
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
