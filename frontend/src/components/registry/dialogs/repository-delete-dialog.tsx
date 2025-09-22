"use client"

import { Trash2Icon } from "lucide-react"
import type { RegistryRepositoryReadMinimal } from "@/client"
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
import { useRegistryRepositories } from "@/lib/hooks"

interface DeleteRepositoryDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedRepo: RegistryRepositoryReadMinimal | null
  setSelectedRepo: (repo: RegistryRepositoryReadMinimal | null) => void
}

export function DeleteRepositoryDialog({
  open,
  onOpenChange,
  selectedRepo,
  setSelectedRepo,
}: DeleteRepositoryDialogProps) {
  const { deleteRepo } = useRegistryRepositories()

  const handleDelete = async () => {
    if (!selectedRepo) {
      console.error("No repository selected")
      return
    }

    try {
      await deleteRepo({ repositoryId: selectedRepo.id })
    } catch (error) {
      console.error("Error deleting repository", error)
    } finally {
      setSelectedRepo(null)
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete repository</AlertDialogTitle>
          <AlertDialogDescription>
            <span className="flex flex-col space-y-2">
              <span>You are about to delete the repository </span>
              <b className="font-mono tracking-tighter">
                {selectedRepo?.origin}
              </b>
              <span>
                Are you sure you want to proceed? This action cannot be undone.
              </span>
              <span className="italic">
                You cannot delete the base Tracecat actions or the custom
                template repositories. If you delete your remote repository, you
                will need to restart the instance to restore it.
              </span>
            </span>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={handleDelete}>
            <div className="flex items-center space-x-2">
              <Trash2Icon className="size-4" />
              <span>Delete</span>
            </div>
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
