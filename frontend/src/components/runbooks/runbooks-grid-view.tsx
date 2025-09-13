"use client"

import { FileText, Search, SortDesc } from "lucide-react"
import { useMemo, useState } from "react"
import type { RunbookRead } from "@/client"
import { RunbookCard } from "@/components/runbooks/runbook-card"
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
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useDeleteRunbook } from "@/hooks/use-runbook"
import { useWorkspaceId } from "@/providers/workspace-id"

export type SortOption = "created_at" | "updated_at"

interface RunbooksGridViewProps {
  runbooks: RunbookRead[]
  isLoading: boolean
  sortBy: SortOption
  onSortChange: (sort: SortOption) => void
}

export function RunbooksGridView({
  runbooks,
  isLoading,
  sortBy,
  onSortChange,
}: RunbooksGridViewProps) {
  const workspaceId = useWorkspaceId()
  const [searchTerm, setSearchTerm] = useState("")
  const [runbookToDelete, setRunbookToDelete] = useState<string | null>(null)
  const { deleteRunbook, deleteRunbookPending } = useDeleteRunbook(workspaceId)

  // Filter runbooks based on search term
  const filteredRunbooks = useMemo(() => {
    if (!searchTerm) return runbooks

    const lowerSearch = searchTerm.toLowerCase()
    return runbooks.filter((runbook) =>
      runbook.title.toLowerCase().includes(lowerSearch)
    )
  }, [runbooks, searchTerm])

  const handleDelete = async () => {
    if (runbookToDelete) {
      await deleteRunbook(runbookToDelete)
      setRunbookToDelete(null)
    }
  }

  const handleDeleteRequest = (runbookId: string) => {
    setRunbookToDelete(runbookId)
  }

  if (!isLoading && runbooks.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center">
        <div className="flex flex-col items-center gap-4 text-center">
          <div className="rounded-full bg-muted p-3">
            <FileText className="h-8 w-8 text-muted-foreground" />
          </div>
          <div className="space-y-2">
            <h3 className="text-lg font-semibold">No runbooks yet</h3>
            <p className="text-sm text-muted-foreground max-w-md">
              Generate your first runbook via case chat or create a new one from
              scratch.
            </p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="h-full">
      <div className="space-y-4">
        {/* Search and Sort Controls */}
        <div className="flex flex-col sm:flex-row gap-4">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="Filter runbooks by title..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="pl-9"
            />
          </div>
          <Select
            value={sortBy}
            onValueChange={(value) => onSortChange(value as SortOption)}
          >
            <SelectTrigger className="w-full sm:w-[180px]">
              <SortDesc className="h-4 w-4" />
              <SelectValue placeholder="Sort by" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="created_at">Created at</SelectItem>
              <SelectItem value="updated_at">Updated at</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {/* Grid of Runbook Cards */}
        {filteredRunbooks.length === 0 && searchTerm ? (
          <div className="flex flex-col items-center justify-center py-12">
            <p className="text-sm text-muted-foreground">
              No runbooks found matching "{searchTerm}"
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {filteredRunbooks.map((runbook) => (
              <RunbookCard
                key={runbook.id}
                runbook={runbook}
                onDelete={handleDeleteRequest}
              />
            ))}
          </div>
        )}
      </div>

      {/* Delete Confirmation Dialog */}
      <AlertDialog
        open={!!runbookToDelete}
        onOpenChange={(open) => !open && setRunbookToDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete runbook</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete this runbook? This action cannot
              be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteRunbookPending}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDelete}
              disabled={deleteRunbookPending}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {deleteRunbookPending ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
