import { Loader2 } from "lucide-react"
import { type FormEvent, useCallback, useState } from "react"
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
import { validateSkillName } from "@/lib/skills-studio"
import { cn } from "@/lib/utils"

type CreateSkillDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  name: string
  onNameChange: (value: string) => void
  description: string
  onDescriptionChange: (value: string) => void
  pending: boolean
  onCreate: () => Promise<void>
}

function normalizeSkillNameInput(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/_/g, "-")
    .replace(/[^a-z0-9-\s]/g, "")
    .replace(/[\s-]+/g, "-")
}

/**
 * Dialog for creating a new empty skill with name and description.
 *
 * @param props Dialog state and callbacks from the parent hook.
 */
export function CreateSkillDialog({
  open,
  onOpenChange,
  name,
  onNameChange,
  description,
  onDescriptionChange,
  pending,
  onCreate,
}: CreateSkillDialogProps) {
  const [nameValidationError, setNameValidationError] = useState<string | null>(
    null
  )

  const handleOpenChange = useCallback(
    (nextOpen: boolean) => {
      if (!nextOpen) {
        setNameValidationError(null)
      }
      onOpenChange(nextOpen)
    },
    [onOpenChange]
  )

  const handleNameChange = useCallback(
    (value: string) => {
      if (nameValidationError !== null) {
        setNameValidationError(null)
      }
      onNameChange(normalizeSkillNameInput(value))
    },
    [nameValidationError, onNameChange]
  )

  const handleSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault()

      const error = validateSkillName(name)
      if (error !== null) {
        setNameValidationError(error)
        return
      }

      setNameValidationError(null)
      await onCreate()
    },
    [name, onCreate]
  )

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create skill</DialogTitle>
          <DialogDescription>
            Start with an empty working copy seeded with a root SKILL.md file.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => void handleSubmit(event)}
        >
          <div className="space-y-3">
            <div className="space-y-2">
              <Label htmlFor="new-skill-name">Name</Label>
              <Input
                id="new-skill-name"
                value={name}
                onChange={(event) => handleNameChange(event.target.value)}
                placeholder="threat-intel"
                aria-invalid={nameValidationError !== null || undefined}
                className={cn(
                  nameValidationError !== null &&
                    "border-destructive focus-visible:ring-destructive"
                )}
              />
              {nameValidationError !== null ? (
                <p className="text-xs text-destructive">
                  {nameValidationError}
                </p>
              ) : (
                <p className="text-xs text-muted-foreground">
                  Lowercase letters, numbers, and single hyphens. Max 64
                  characters.
                </p>
              )}
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
            <Button type="submit" disabled={pending}>
              {pending ? (
                <Loader2 className="mr-2 size-4 animate-spin" />
              ) : null}
              Create skill
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
