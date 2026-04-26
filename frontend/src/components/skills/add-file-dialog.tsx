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

type AddFileDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  filePath: string
  onFilePathChange: (value: string) => void
  onCreateFile: () => void
}

/**
 * Dialog for adding a new inline file to the working copy.
 *
 * @param props Dialog state and callbacks from the parent hook.
 */
export function AddFileDialog({
  open,
  onOpenChange,
  filePath,
  onFilePathChange,
  onCreateFile,
}: AddFileDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add file</DialogTitle>
          <DialogDescription>
            Only Markdown and Python files can be created inline in this first
            pass.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="new-file-path">Relative path</Label>
          <Input
            id="new-file-path"
            value={filePath}
            onChange={(event) => onFilePathChange(event.target.value)}
            placeholder="helpers/fetch_data.py"
          />
        </div>
        <DialogFooter>
          <Button onClick={onCreateFile} disabled={!filePath.trim()}>
            Add file
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
