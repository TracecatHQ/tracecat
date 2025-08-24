"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type { ColumnDef, Row } from "@tanstack/react-table"
import {
  CheckCircle,
  Copy,
  GitBranch,
  GitCommitHorizontal,
  GitMerge,
  Network,
  Pencil,
  Trash2,
  XCircle,
} from "lucide-react"
import { useSearchParams } from "next/navigation"
import { useEffect, useMemo, useState } from "react"
import {
  entitiesArchiveRelation,
  entitiesDeleteRelation,
  entitiesListAllRelations,
  entitiesRestoreRelation,
  entitiesUpdateRelation,
  type RelationDefinitionRead,
} from "@/client"
import { DataTable, DataTableColumnHeader } from "@/components/data-table"
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
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { useLocalStorage } from "@/lib/hooks"
import { useEntities } from "@/lib/hooks/use-entities"
import { getIconByName } from "@/lib/icon-data"
import { shortTimeAgo } from "@/lib/utils"
import { useWorkspace } from "@/providers/workspace"

const relationTypeConfig = {
  one_to_one: {
    label: "One to one",
    icon: GitCommitHorizontal,
    description: "Each record can relate to exactly one other record",
  },
  one_to_many: {
    label: "One to many",
    icon: GitBranch,
    description: "One record can relate to multiple records",
  },
  many_to_one: {
    label: "Many to one",
    icon: GitMerge,
    description: "Multiple records can relate to one record",
  },
  many_to_many: {
    label: "Many to many",
    icon: Network,
    description: "Multiple records can relate to multiple records",
  },
}

export function RelationsWorkspaceTable() {
  const { workspaceId } = useWorkspace()
  const queryClient = useQueryClient()
  const { entities } = useEntities(workspaceId || "", true)
  const searchParams = useSearchParams()

  const [sourceFilter, setSourceFilter] = useState<string | null>(null)
  const [targetFilter, setTargetFilter] = useState<string | null>(null)
  const [includeInactive] = useLocalStorage("entities-include-inactive", false)

  // Initialize filters from URL (e.g., ?source=...&target=...)
  useEffect(() => {
    const s = searchParams?.get("source")
    const t = searchParams?.get("target")
    if (s) setSourceFilter(s)
    if (t) setTargetFilter(t)
  }, [])

  const entityById = useMemo(() => {
    const map = new Map<string, { display_name: string; icon?: string }>()
    ;(entities || []).forEach((e) =>
      map.set(e.id, {
        display_name: e.display_name,
        icon: e.icon || undefined,
      })
    )
    return map
  }, [entities])

  const {
    data: relations,
    isLoading,
    error,
  } = useQuery({
    queryKey: [
      "workspace-relations",
      workspaceId,
      sourceFilter,
      targetFilter,
      includeInactive,
    ],
    queryFn: async () => {
      if (!workspaceId) return [] as RelationDefinitionRead[]
      const res = await entitiesListAllRelations({
        workspaceId,
        sourceEntityId: sourceFilter || undefined,
        targetEntityId: targetFilter || undefined,
        includeInactive,
      })
      return res
    },
    enabled: !!workspaceId,
  })

  const { mutateAsync: doDeactivate } = useMutation({
    mutationFn: async (relationId: string) =>
      entitiesArchiveRelation({ workspaceId: workspaceId!, relationId }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["workspace-relations", workspaceId],
      })
      toast({ title: "Relation archived" })
    },
    onError: () =>
      toast({ title: "Failed to archive relation", variant: "destructive" }),
  })

  const { mutateAsync: doReactivate } = useMutation({
    mutationFn: async (relationId: string) =>
      entitiesRestoreRelation({ workspaceId: workspaceId!, relationId }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["workspace-relations", workspaceId],
      })
      toast({ title: "Relation restored" })
    },
    onError: () =>
      toast({ title: "Failed to restore relation", variant: "destructive" }),
  })

  const { mutateAsync: doDelete } = useMutation({
    mutationFn: async (relationId: string) =>
      entitiesDeleteRelation({ workspaceId: workspaceId!, relationId }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["workspace-relations", workspaceId],
      })
      toast({ title: "Relation deleted" })
    },
    onError: () =>
      toast({ title: "Failed to delete relation", variant: "destructive" }),
  })

  const { mutateAsync: doUpdate } = useMutation({
    mutationFn: async (args: {
      relationId: string
      display_name?: string
      source_key?: string
    }) =>
      entitiesUpdateRelation({
        workspaceId: workspaceId!,
        relationId: args.relationId,
        requestBody: {
          display_name: args.display_name,
          source_key: args.source_key,
        },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["workspace-relations", workspaceId],
      })
      toast({ title: "Relation updated" })
    },
    onError: () =>
      toast({ title: "Failed to update relation", variant: "destructive" }),
  })

  const [selectedRelation, setSelectedRelation] =
    useState<RelationDefinitionRead | null>(null)
  const [actionType, setActionType] = useState<"delete" | "deactivate" | null>(
    null
  )

  const columns: ColumnDef<RelationDefinitionRead, unknown>[] = [
    {
      accessorKey: "display_name",
      header: ({ column }) => (
        <DataTableColumnHeader
          className="text-xs"
          column={column}
          title="Relation"
        />
      ),
      cell: ({ row }) => (
        <div>
          <div className="font-medium text-sm">{row.original.display_name}</div>
          <div className="text-xs text-muted-foreground">
            {row.original.source_key}
          </div>
        </div>
      ),
      enableSorting: true,
      enableHiding: false,
    },
    {
      id: "source",
      accessorFn: (row: RelationDefinitionRead) => row.source_entity_id,
      header: ({ column }) => (
        <DataTableColumnHeader
          className="text-xs"
          column={column}
          title="Source"
        />
      ),
      cell: ({ row }) => {
        const entity = entityById.get(row.original.source_entity_id)
        const IconComponent = entity?.icon ? getIconByName(entity.icon) : null
        const initials = entity?.display_name?.[0]?.toUpperCase() || "?"

        return (
          <div className="flex items-center gap-2">
            <Avatar className="size-6 shrink-0">
              <AvatarFallback className="text-xs">
                {IconComponent ? (
                  <IconComponent className="size-3" />
                ) : (
                  initials
                )}
              </AvatarFallback>
            </Avatar>
            <div className="text-xs">
              {entity?.display_name || row.original.source_entity_id}
            </div>
          </div>
        )
      },
      enableSorting: true,
      enableHiding: false,
      filterFn: (
        row: Row<RelationDefinitionRead>,
        _id: string,
        value: string[]
      ) => {
        const v = row.getValue("source") as string
        return value.includes(v)
      },
    },
    {
      id: "target",
      accessorFn: (row: RelationDefinitionRead) => row.target_entity_id,
      header: ({ column }) => (
        <DataTableColumnHeader
          className="text-xs"
          column={column}
          title="Target"
        />
      ),
      cell: ({ row }) => {
        const entity = entityById.get(row.original.target_entity_id)
        const IconComponent = entity?.icon ? getIconByName(entity.icon) : null
        const initials = entity?.display_name?.[0]?.toUpperCase() || "?"

        return (
          <div className="flex items-center gap-2">
            <Avatar className="size-6 shrink-0">
              <AvatarFallback className="text-xs">
                {IconComponent ? (
                  <IconComponent className="size-3" />
                ) : (
                  initials
                )}
              </AvatarFallback>
            </Avatar>
            <div className="text-xs">
              {entity?.display_name || row.original.target_entity_id}
            </div>
          </div>
        )
      },
      enableSorting: true,
      enableHiding: false,
      filterFn: (
        row: Row<RelationDefinitionRead>,
        _id: string,
        value: string[]
      ) => {
        const v = row.getValue("target") as string
        return value.includes(v)
      },
    },
    {
      accessorKey: "relation_type",
      header: ({ column }) => (
        <DataTableColumnHeader
          className="text-xs"
          column={column}
          title="Type"
        />
      ),
      cell: ({ row }) => {
        const relationType = row.original
          .relation_type as keyof typeof relationTypeConfig
        const config = relationTypeConfig[relationType]
        const Icon = config?.icon

        return (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge variant="secondary" className="text-xs">
                  {Icon && <Icon className="mr-1.5 h-3 w-3" />}
                  {config?.label || relationType}
                </Badge>
              </TooltipTrigger>
              <TooltipContent>
                <p className="text-xs">{config?.description}</p>
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )
      },
      enableSorting: true,
      enableHiding: false,
      filterFn: (
        row: Row<RelationDefinitionRead>,
        _id: string,
        value: string[]
      ) => {
        const v = row.getValue("relation_type") as string
        return value.includes(v)
      },
    },
    {
      id: "status",
      accessorFn: (row: RelationDefinitionRead) =>
        row.is_active ? "active" : "inactive",
      header: ({ column }) => (
        <DataTableColumnHeader
          className="text-xs"
          column={column}
          title="Status"
        />
      ),
      cell: ({ row }) => (
        <Badge
          variant={row.original.is_active ? "default" : "secondary"}
          className="text-xs"
        >
          {row.original.is_active ? "Active" : "Inactive"}
        </Badge>
      ),
      enableSorting: true,
      enableHiding: false,
      enableColumnFilter: true,
      filterFn: (
        row: Row<RelationDefinitionRead>,
        _id: string,
        value: string[]
      ) => {
        const status = row.original.is_active ? "active" : "inactive"
        return value.includes(status)
      },
    },
    {
      accessorKey: "created_at",
      header: ({ column }) => (
        <DataTableColumnHeader
          className="text-xs"
          column={column}
          title="Created"
        />
      ),
      cell: ({ row }) => (
        <div className="text-xs text-muted-foreground">
          {shortTimeAgo(new Date(row.original.created_at))}
        </div>
      ),
      enableSorting: true,
      enableHiding: false,
    },
    {
      accessorKey: "updated_at",
      header: ({ column }) => (
        <DataTableColumnHeader
          className="text-xs"
          column={column}
          title="Updated"
        />
      ),
      cell: ({ row }) => (
        <div className="text-xs text-muted-foreground">
          {row.original.updated_at
            ? shortTimeAgo(new Date(row.original.updated_at))
            : "-"}
        </div>
      ),
      enableSorting: true,
      enableHiding: false,
    },
    {
      id: "actions",
      enableHiding: false,
      cell: ({ row }) => (
        <div className="flex justify-end">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                className="size-8 p-0"
                onClick={(e) => e.stopPropagation()}
              >
                <span className="sr-only">Open menu</span>
                <DotsHorizontalIcon className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem
                onClick={(e) => {
                  e.stopPropagation()
                  navigator.clipboard.writeText(row.original.id)
                }}
              >
                <Copy className="mr-2 h-3 w-3" />
                Copy relation ID
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={async (e) => {
                  e.stopPropagation()
                  // Simple edit - just rename for now
                  const newName = window.prompt(
                    "Enter new display name:",
                    row.original.display_name
                  )
                  if (newName && newName !== row.original.display_name) {
                    await doUpdate({
                      relationId: row.original.id,
                      display_name: newName,
                    })
                  }
                }}
              >
                <Pencil className="mr-2 h-3 w-3" />
                Edit relation
              </DropdownMenuItem>
              {row.original.is_active ? (
                <>
                  <DropdownMenuSeparator />
                  <AlertDialogTrigger asChild>
                    <DropdownMenuItem
                      className="text-rose-500 focus:text-rose-600"
                      onClick={(e) => {
                        e.stopPropagation()
                        setSelectedRelation(row.original)
                        setActionType("deactivate")
                      }}
                    >
                      <XCircle className="mr-2 h-3 w-3" />
                      Archive relation
                    </DropdownMenuItem>
                  </AlertDialogTrigger>
                </>
              ) : (
                <>
                  <DropdownMenuItem
                    onClick={(e) => {
                      e.stopPropagation()
                      void doReactivate(row.original.id)
                    }}
                  >
                    <CheckCircle className="mr-2 h-3 w-3" />
                    Restore relation
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <AlertDialogTrigger asChild>
                    <DropdownMenuItem
                      className="text-rose-500 focus:text-rose-600"
                      onClick={(e) => {
                        e.stopPropagation()
                        setSelectedRelation(row.original)
                        setActionType("delete")
                      }}
                    >
                      <Trash2 className="mr-2 h-3 w-3" />
                      Delete relation
                    </DropdownMenuItem>
                  </AlertDialogTrigger>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      ),
    },
  ]

  return (
    <AlertDialog
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          setSelectedRelation(null)
          setActionType(null)
        }
      }}
    >
      <DataTable<RelationDefinitionRead, unknown>
        data={relations || []}
        columns={columns}
        isLoading={isLoading}
        error={error instanceof Error ? error : null}
        emptyMessage="No relations found."
        toolbarProps={{
          filterProps: {
            placeholder: "Filter relations...",
            column: "display_name",
          },
          fields: [
            {
              column: "status",
              title: "Status",
              options: [
                { label: "Active", value: "active" },
                { label: "Inactive", value: "inactive" },
              ],
            },
            {
              column: "relation_type",
              title: "Type",
              options: [
                { label: "One to one", value: "one_to_one" },
                { label: "One to many", value: "one_to_many" },
                { label: "Many to one", value: "many_to_one" },
                { label: "Many to many", value: "many_to_many" },
              ],
            },
            {
              column: "source",
              title: "Source",
              options: (entities || []).map((e) => ({
                label: e.display_name,
                value: e.id,
              })),
            },
            {
              column: "target",
              title: "Target",
              options: (entities || []).map((e) => ({
                label: e.display_name,
                value: e.id,
              })),
            },
          ],
        }}
      />
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {actionType === "delete"
              ? "Delete relation permanently"
              : "Archive relation"}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {actionType === "delete" ? (
              <>
                Are you sure you want to permanently delete the relation{" "}
                <strong>{selectedRelation?.display_name}</strong>? This action
                cannot be undone. All associated data will be permanently
                deleted.
              </>
            ) : (
              <>
                Are you sure you want to archive the relation{" "}
                <strong>{selectedRelation?.display_name}</strong>? The relation
                will be hidden but data will be preserved. You can restore it
                later.
              </>
            )}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant={actionType === "delete" ? "destructive" : "default"}
            onClick={async () => {
              if (selectedRelation) {
                try {
                  if (actionType === "delete") {
                    await doDelete(selectedRelation.id)
                  } else if (actionType === "deactivate") {
                    await doDeactivate(selectedRelation.id)
                  }
                  setSelectedRelation(null)
                  setActionType(null)
                } catch (error) {
                  console.error(`Failed to ${actionType} relation:`, error)
                }
              }
            }}
          >
            {actionType === "delete" ? "Delete Permanently" : "Archive"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
