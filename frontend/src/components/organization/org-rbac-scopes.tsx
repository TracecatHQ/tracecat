"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { KeyIcon, PlusIcon, SearchIcon } from "lucide-react"
import { useMemo, useState } from "react"
import type { ScopeRead, ScopeSource } from "@/client"
import {
  RbacDetailRow,
  RbacListContainer,
  RbacListEmpty,
  RbacListHeader,
  RbacListItem,
} from "@/components/organization/rbac-list-item"
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
import { Skeleton } from "@/components/ui/skeleton"
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
  const [expandedScopeId, setExpandedScopeId] = useState<string | null>(null)
  const [isCreateOpen, setIsCreateOpen] = useState(false)
  const [sourceFilter, setSourceFilter] = useState<ScopeSource | "all">("all")
  const [searchQuery, setSearchQuery] = useState("")
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

  const filteredScopes = useMemo(() => {
    if (!searchQuery.trim()) return scopes
    const query = searchQuery.toLowerCase()
    return scopes.filter(
      (scope) =>
        scope.name.toLowerCase().includes(query) ||
        scope.description?.toLowerCase().includes(query) ||
        scope.resource.toLowerCase().includes(query) ||
        scope.action.toLowerCase().includes(query)
    )
  }, [scopes, searchQuery])

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

  if (error) {
    return (
      <div className="flex items-center justify-center py-12 text-sm text-destructive">
        Failed to load scopes
      </div>
    )
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
          <RbacListHeader
            left={
              <div className="flex items-center gap-3">
                <div className="relative">
                  <SearchIcon className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    placeholder="Search scopes..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="h-9 w-[200px] pl-8"
                  />
                </div>
                <Select
                  value={sourceFilter}
                  onValueChange={(v) =>
                    setSourceFilter(v as ScopeSource | "all")
                  }
                >
                  <SelectTrigger className="h-9 w-[130px]">
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
            }
            right={
              <DialogTrigger asChild>
                <Button size="sm">
                  <PlusIcon className="mr-2 size-4" />
                  Create scope
                </Button>
              </DialogTrigger>
            }
          />

          {isLoading ? (
            <RbacListContainer>
              {[1, 2, 3].map((i) => (
                <div
                  key={i}
                  className="flex items-center gap-3 border-b border-border/50 px-3 py-2.5 last:border-b-0"
                >
                  <Skeleton className="size-6" />
                  <Skeleton className="size-4" />
                  <div className="flex-1 space-y-1.5">
                    <Skeleton className="h-4 w-32" />
                    <Skeleton className="h-3 w-48" />
                  </div>
                </div>
              ))}
            </RbacListContainer>
          ) : filteredScopes.length === 0 ? (
            <RbacListContainer>
              <RbacListEmpty
                message={
                  searchQuery || sourceFilter !== "all"
                    ? "No scopes match your filters"
                    : "No scopes found"
                }
              />
            </RbacListContainer>
          ) : (
            <RbacListContainer>
              {filteredScopes.map((scope) => (
                <RbacListItem
                  key={scope.id}
                  icon={<KeyIcon className="size-4" />}
                  title={
                    <code className="text-xs font-mono">{scope.name}</code>
                  }
                  subtitle={
                    scope.description || `${scope.resource}:${scope.action}`
                  }
                  badges={
                    <Badge
                      variant="secondary"
                      className={`text-[10px] ${SCOPE_SOURCE_COLORS[scope.source]}`}
                    >
                      {scope.source}
                    </Badge>
                  }
                  isExpanded={expandedScopeId === scope.id}
                  onExpandedChange={(expanded) =>
                    setExpandedScopeId(expanded ? scope.id : null)
                  }
                  actions={
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          variant="ghost"
                          className="size-8 p-0 opacity-0 transition-opacity group-hover:opacity-100 data-[state=open]:opacity-100"
                        >
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
                        {scope.source === "custom" && (
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
                  }
                >
                  <div className="space-y-3">
                    <RbacDetailRow label="Name">
                      <code className="rounded bg-muted/60 px-1.5 py-0.5 text-[11px] font-mono">
                        {scope.name}
                      </code>
                    </RbacDetailRow>
                    <RbacDetailRow label="Resource">
                      <span className="text-muted-foreground">
                        {scope.resource}
                      </span>
                    </RbacDetailRow>
                    <RbacDetailRow label="Action">
                      <span className="text-muted-foreground">
                        {scope.action}
                      </span>
                    </RbacDetailRow>
                    <RbacDetailRow label="Source">
                      <Badge
                        variant="secondary"
                        className={SCOPE_SOURCE_COLORS[scope.source]}
                      >
                        {scope.source}
                      </Badge>
                    </RbacDetailRow>
                    {scope.description && (
                      <RbacDetailRow label="Description">
                        {scope.description}
                      </RbacDetailRow>
                    )}
                  </div>
                </RbacListItem>
              ))}
            </RbacListContainer>
          )}
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
