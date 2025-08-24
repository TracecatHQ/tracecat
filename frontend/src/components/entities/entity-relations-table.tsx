"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import {
  Copy,
  GitBranch,
  GitCommitHorizontal,
  GitMerge,
  Network,
  Pencil,
  Trash2,
  XCircle,
} from "lucide-react"
import { useMemo, useState } from "react"
import type { RelationDefinitionRead } from "@/client"
import {
  entitiesDeactivateRelation,
  entitiesDeleteRelation,
  entitiesReactivateRelation,
  entitiesUpdateRelation,
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
import { toast } from "@/components/ui/use-toast"
import { useEntities, useEntityRelations } from "@/lib/hooks/use-entities"
import { getIconByName } from "@/lib/icon-data"
import { useWorkspace } from "@/providers/workspace"

const relationTypeConfig = {
  one_to_one: {
    label: "One to one",
    icon: GitCommitHorizontal,
  },
  one_to_many: {
    label: "One to many",
    icon: GitBranch,
  },
  many_to_one: {
    label: "Many to one",
    icon: GitMerge,
  },
  many_to_many: {
    label: "Many to many",
    icon: Network,
  },
}

export function EntityRelationsTable({ entityId }: { entityId: string }) {
  const { workspaceId } = useWorkspace()
  const queryClient = useQueryClient()
  const { relations, relationsIsLoading } = useEntityRelations(
    workspaceId || "",
    entityId
  )
  const { entities } = useEntities(workspaceId || "", true)

  const entityById = useMemo(() => {
    const map = new Map<string, { display_name: string; icon?: string }>()
    ;(entities || []).forEach((e) =>
      map.set(e.id, { display_name: e.display_name, icon: e.icon || undefined })
    )
    return map
  }, [entities])

  const { mutateAsync: doDeactivate } = useMutation({
    mutationFn: async (relationId: string) =>
      entitiesDeactivateRelation({ workspaceId: workspaceId!, relationId }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["entity-relations", workspaceId, entityId],
      })
      toast({ title: "Relation deactivated" })
    },
  })

  const { mutateAsync: doReactivate } = useMutation({
    mutationFn: async (relationId: string) =>
      entitiesReactivateRelation({ workspaceId: workspaceId!, relationId }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["entity-relations", workspaceId, entityId],
      })
      toast({ title: "Relation reactivated" })
    },
  })

  const { mutateAsync: doDelete } = useMutation({
    mutationFn: async (relationId: string) =>
      entitiesDeleteRelation({ workspaceId: workspaceId!, relationId }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["entity-relations", workspaceId, entityId],
      })
      toast({ title: "Relation deleted" })
    },
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
        queryKey: ["entity-relations", workspaceId, entityId],
      })
      toast({ title: "Relation updated" })
    },
  })

  const [selectedRelation, setSelectedRelation] =
    useState<RelationDefinitionRead | null>(null)
  const [actionType, setActionType] = useState<"delete" | "deactivate" | null>(
    null
  )

  if (relationsIsLoading) {
    return (
      <div className="text-xs text-muted-foreground">Loading relations…</div>
    )
  }

  return (
    <>
      <AlertDialog
        onOpenChange={(isOpen) => {
          if (!isOpen) setSelectedRelation(null)
        }}
      >
        <DataTable
          data={(relations || []) as RelationDefinitionRead[]}
          columns={[
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
                  <div className="font-medium text-sm">
                    {row.original.display_name}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {row.original.source_key}
                  </div>
                </div>
              ),
              enableSorting: true,
              enableHiding: false,
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
                const IconComponent = entity?.icon
                  ? getIconByName(entity.icon)
                  : null
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
                const cfg = relationTypeConfig[relationType]
                const Icon = cfg?.icon
                return (
                  <Badge variant="secondary" className="text-xs">
                    {Icon && <Icon className="mr-1.5 h-3 w-3" />}
                    {cfg?.label || relationType}
                  </Badge>
                )
              },
              enableSorting: true,
              enableHiding: false,
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
            },
            {
              id: "actions",
              enableHiding: false,
              cell: ({ row }) => (
                <div className="flex justify-end">
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
                          navigator.clipboard.writeText(row.original.id)
                        }
                      >
                        <Copy className="mr-2 h-3 w-3" /> Copy relation ID
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onClick={async () => {
                          // Quick rename
                          const newName = window.prompt(
                            "Enter new display name:",
                            row.original.display_name
                          )
                          if (
                            newName &&
                            newName !== row.original.display_name
                          ) {
                            await doUpdate({
                              relationId: row.original.id,
                              display_name: newName,
                            })
                          }
                        }}
                      >
                        <Pencil className="mr-2 h-3 w-3" /> Edit relation
                      </DropdownMenuItem>
                      {row.original.is_active ? (
                        <>
                          <DropdownMenuSeparator />
                          <AlertDialogTrigger asChild>
                            <DropdownMenuItem
                              className="text-rose-500 focus:text-rose-600"
                              onClick={() => {
                                setSelectedRelation(row.original)
                                setActionType("deactivate")
                              }}
                            >
                              <XCircle className="mr-2 h-3 w-3" /> Deactivate
                              relation
                            </DropdownMenuItem>
                          </AlertDialogTrigger>
                        </>
                      ) : (
                        <DropdownMenuItem
                          onClick={() => void doReactivate(row.original.id)}
                        >
                          <GitCommitHorizontal className="mr-2 h-3 w-3" />{" "}
                          Reactivate relation
                        </DropdownMenuItem>
                      )}
                      <DropdownMenuSeparator />
                      <AlertDialogTrigger asChild>
                        <DropdownMenuItem
                          className="text-rose-500 focus:text-rose-600"
                          onClick={() => {
                            setSelectedRelation(row.original)
                            setActionType("delete")
                          }}
                        >
                          <Trash2 className="mr-2 h-3 w-3" /> Delete relation
                        </DropdownMenuItem>
                      </AlertDialogTrigger>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              ),
            },
          ]}
          toolbarProps={undefined}
          onRowClick={undefined}
          emptyState={{
            title: "No relations",
            description: "Create a relation to link this entity to others.",
          }}
        />

        {/* Confirmations */}
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {actionType === "delete"
                ? "Delete relation?"
                : "Deactivate relation?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {actionType === "delete"
                ? "This permanently deletes the relation and its links."
                : "This relation will be disabled for new links and forms."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            {actionType === "delete" ? (
              <AlertDialogAction
                className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                onClick={async () => {
                  if (selectedRelation) await doDelete(selectedRelation.id)
                }}
              >
                Delete
              </AlertDialogAction>
            ) : (
              <AlertDialogAction
                onClick={async () => {
                  if (selectedRelation) await doDeactivate(selectedRelation.id)
                }}
              >
                Deactivate
              </AlertDialogAction>
            )}
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
