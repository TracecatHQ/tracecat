"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { useState } from "react"
import type { ScopeRead, ScopeSource } from "@/client"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { useRbacScopes } from "@/lib/hooks"

const SCOPE_SOURCE_COLORS: Record<ScopeSource, string> = {
  system: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300",
  registry:
    "bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-300",
  custom: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300",
}

export function OrgRbacScopes() {
  const [selectedScope, setSelectedScope] = useState<ScopeRead | null>(null)
  const [isCreateOpen, setIsCreateOpen] = useState(false)
  const [sourceFilter, setSourceFilter] = useState<ScopeSource | "all">("all")
  const {
    scopes,
    isLoading,
    error,
    createScope,
    createScopeIsPending,
    deleteScope,
    deleteScopeIsPending,
  } = useRbacScopes({
    includeSystem: true,
    source: sourceFilter === "all" ? undefined : sourceFilter,
  })

  const handleCreateScope = async (name: string, description: string) => {
    await createScope({ name, description: description || undefined })
    setIsCreateOpen(false)
  }

  const handleDeleteScope = async () => {
    if (selectedScope) {
      await deleteScope(selectedScope.id)
      setSelectedScope(null)
    }
  }

  return (
    <Dialog open={isCreateOpen} onOpenChange={setIsCreateOpen}>
      <AlertDialog
        onOpenChange={(isOpen) => {
          if (!isOpen) {
            setSelectedScope(null)
          }
        }}
      >
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <Select
                value={sourceFilter}
                onValueChange={(v) => setSourceFilter(v as ScopeSource | "all")}
              >
                <SelectTrigger className="w-[150px]">
                  <SelectValue placeholder="Filter by source" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All sources</SelectItem>
                  <SelectItem value="system">System</SelectItem>
                  <SelectItem value="registry">Registry</SelectItem>
                  <SelectItem value="custom">Custom</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <DialogTrigger asChild>
              <Button size="sm">Create scope</Button>
            </DialogTrigger>
          </div>

          <DataTable
            data={scopes}
            isLoading={isLoading}
            error={error as Error | null}
            emptyMessage="No scopes found"
            initialSortingState={[{ id: "name", desc: false }]}
            columns={[
              {
                accessorKey: "name",
                header: ({ column }) => (
                  <DataTableColumnHeader
                    className="text-xs"
                    column={column}
                    title="Scope"
                  />
                ),
                cell: ({ row }) => (
                  <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
                    {row.getValue<string>("name")}
                  </code>
                ),
                enableSorting: true,
                enableHiding: false,
              },
              {
                accessorKey: "resource",
                header: ({ column }) => (
                  <DataTableColumnHeader
                    className="text-xs"
                    column={column}
                    title="Resource"
                  />
                ),
                cell: ({ row }) => (
                  <span className="text-xs text-muted-foreground">
                    {row.getValue<string>("resource")}
                  </span>
                ),
                enableSorting: true,
                enableHiding: true,
              },
              {
                accessorKey: "action",
                header: ({ column }) => (
                  <DataTableColumnHeader
                    className="text-xs"
                    column={column}
                    title="Action"
                  />
                ),
                cell: ({ row }) => (
                  <span className="text-xs text-muted-foreground">
                    {row.getValue<string>("action")}
                  </span>
                ),
                enableSorting: true,
                enableHiding: true,
              },
              {
                accessorKey: "source",
                header: ({ column }) => (
                  <DataTableColumnHeader
                    className="text-xs"
                    column={column}
                    title="Source"
                  />
                ),
                cell: ({ row }) => {
                  const source = row.getValue<ScopeSource>("source")
                  return (
                    <Badge
                      variant="secondary"
                      className={SCOPE_SOURCE_COLORS[source]}
                    >
                      {source}
                    </Badge>
                  )
                },
                enableSorting: true,
                enableHiding: true,
              },
              {
                accessorKey: "description",
                header: ({ column }) => (
                  <DataTableColumnHeader
                    className="text-xs"
                    column={column}
                    title="Description"
                  />
                ),
                cell: ({ row }) => (
                  <span className="text-xs text-muted-foreground line-clamp-1">
                    {row.getValue<string>("description") || "-"}
                  </span>
                ),
                enableSorting: false,
                enableHiding: true,
              },
              {
                id: "actions",
                enableHiding: false,
                cell: ({ row }) => {
                  const scope = row.original
                  const isCustom = scope.source === "custom"

                  return (
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" className="size-8 p-0">
                          <span className="sr-only">Open menu</span>
                          <DotsHorizontalIcon className="size-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem
                          onClick={() =>
                            navigator.clipboard.writeText(scope.name)
                          }
                        >
                          Copy scope name
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onClick={() =>
                            navigator.clipboard.writeText(scope.id)
                          }
                        >
                          Copy scope ID
                        </DropdownMenuItem>
                        {isCustom && (
                          <AlertDialogTrigger asChild>
                            <DropdownMenuItem
                              className="text-rose-500 focus:text-rose-600"
                              onClick={() => setSelectedScope(scope)}
                            >
                              Delete scope
                            </DropdownMenuItem>
                          </AlertDialogTrigger>
                        )}
                      </DropdownMenuContent>
                    </DropdownMenu>
                  )
                },
              },
            ]}
            toolbarProps={toolbarProps}
          />
        </div>

        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete scope</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete the scope{" "}
              <code className="rounded bg-muted px-1 py-0.5">
                {selectedScope?.name}
              </code>
              ? This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={handleDeleteScope}
              disabled={deleteScopeIsPending}
            >
              {deleteScopeIsPending ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <CreateScopeDialog
        onSubmit={handleCreateScope}
        isPending={createScopeIsPending}
        onOpenChange={setIsCreateOpen}
      />
    </Dialog>
  )
}

function CreateScopeDialog({
  onSubmit,
  isPending,
  onOpenChange,
}: {
  onSubmit: (name: string, description: string) => Promise<void>
  isPending: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    await onSubmit(name.trim(), description.trim())
    setName("")
    setDescription("")
  }

  return (
    <DialogContent>
      <form onSubmit={handleSubmit}>
        <DialogHeader>
          <DialogTitle>Create custom scope</DialogTitle>
          <DialogDescription>
            Create a custom scope for your organization. Scope names should
            follow the format <code>resource:action</code> (e.g.,{" "}
            <code>workflow:execute</code>).
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-4">
          <div className="space-y-2">
            <Label htmlFor="scope-name">Scope name</Label>
            <Input
              id="scope-name"
              placeholder="e.g., custom:my-scope:read"
              value={name}
              onChange={(e) => setName(e.target.value)}
              pattern="^[a-z0-9:_.*-]+$"
              required
            />
            <p className="text-xs text-muted-foreground">
              Only lowercase letters, numbers, colons, underscores, dots,
              asterisks, and hyphens allowed.
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="scope-description">Description (optional)</Label>
            <Textarea
              id="scope-description"
              placeholder="Describe what this scope grants access to"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
            />
          </div>
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button type="submit" disabled={!name.trim() || isPending}>
            {isPending ? "Creating..." : "Create scope"}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  )
}

const toolbarProps: DataTableToolbarProps<ScopeRead> = {
  filterProps: {
    placeholder: "Filter scopes...",
    column: "name",
  },
}
