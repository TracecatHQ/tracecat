"use client"

import { Loader2 } from "lucide-react"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"

/**
 * Confirm uninstalling a library entry: one skill, or every skill in a group.
 *
 * @param props Dialog state, provider details, and the confirm handler.
 * @returns The confirmation dialog.
 */
export function SkillLibraryUninstallDialog({
  open,
  onOpenChange,
  providerName,
  skillCount,
  pending,
  onConfirm,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  providerName: string
  skillCount: number
  pending: boolean
  onConfirm: () => Promise<void>
}) {
  const single = skillCount === 1
  return (
    <AlertDialog
      open={open}
      onOpenChange={(isOpen) => {
        if (!pending) onOpenChange(isOpen)
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {single
              ? `Uninstall ${providerName}?`
              : `Uninstall all ${providerName} skills?`}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {single
              ? "This removes the skill from this workspace. If an agent still uses it, the uninstall is rejected and the skill stays installed."
              : `This removes ${skillCount} skills from this workspace. If an agent still uses any of them, the entire uninstall is rejected and all skills stay installed.`}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            disabled={pending}
            onClick={(event) => {
              event.preventDefault()
              void onConfirm()
            }}
            variant="destructive"
          >
            {pending && <Loader2 className="mr-2 size-4 animate-spin" />}
            {single ? "Uninstall" : "Uninstall all"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
