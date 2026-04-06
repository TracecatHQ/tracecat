import { Loader2 } from "lucide-react"
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
import { Textarea } from "@/components/ui/textarea"
import { slugify } from "@/lib/utils"

type CreateSkillDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  onTitleChange: (value: string) => void
  slug: string
  onSlugChange: (value: string) => void
  description: string
  onDescriptionChange: (value: string) => void
  pending: boolean
  onCreate: () => Promise<void>
}

/**
 * Dialog for creating a new empty skill with title, slug, and description.
 *
 * @param props Dialog state and callbacks from the parent hook.
 */
export function CreateSkillDialog({
  open,
  onOpenChange,
  title,
  onTitleChange,
  slug,
  onSlugChange,
  description,
  onDescriptionChange,
  pending,
  onCreate,
}: CreateSkillDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create skill</DialogTitle>
          <DialogDescription>
            Start with an empty working copy seeded with a root SKILL.md file.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-2">
            <Label htmlFor="new-skill-title">Title</Label>
            <Input
              id="new-skill-title"
              value={title}
              onChange={(event) => {
                onTitleChange(event.target.value)
                if (!slug) {
                  onSlugChange(slugify(event.target.value, "-"))
                }
              }}
              placeholder="Threat Intel"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="new-skill-slug">Slug</Label>
            <Input
              id="new-skill-slug"
              value={slug}
              onChange={(event) => onSlugChange(event.target.value)}
              placeholder="threat-intel"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="new-skill-description">Description</Label>
            <Textarea
              id="new-skill-description"
              value={description}
              onChange={(event) => onDescriptionChange(event.target.value)}
              placeholder="Optional description"
              rows={3}
            />
          </div>
        </div>
        <DialogFooter>
          <Button
            onClick={() => void onCreate()}
            disabled={pending || !(slug.trim() || title.trim())}
          >
            {pending ? <Loader2 className="mr-2 size-4 animate-spin" /> : null}
            Create skill
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
