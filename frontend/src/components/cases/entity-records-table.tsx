"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import {
  type ColumnDef,
  type ColumnFiltersState,
  flexRender,
  getCoreRowModel,
  getFacetedRowModel,
  getFacetedUniqueValues,
  getFilteredRowModel,
  useReactTable,
} from "@tanstack/react-table"
import {
  ChevronDown,
  Database,
  Edit,
  Loader2,
  Plus,
  Trash2,
  Unlink,
} from "lucide-react"
import { useMemo, useState } from "react"
import JsonView from "react18-json-view"
import type { CaseEntityRead, CaseRecordLinkRead } from "@/client"
import { DataTableFacetedFilter } from "@/components/data-table/faceted-filter"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { getIconByName } from "@/lib/icon-data"
import { shortTimeAgo } from "@/lib/utils"

import "react18-json-view/src/style.css"

interface EntityRecordsTableProps {
  records: CaseRecordLinkRead[]
  isLoading?: boolean
  onEdit?: (record: CaseRecordLinkRead) => void
  onDelete?: (record: CaseRecordLinkRead) => void
  onRemoveLink?: (record: CaseRecordLinkRead) => void
  onAddEntity?: (entityId: string) => void
  entities?: CaseEntityRead[]
  isLoadingEntities?: boolean
}

export function EntityRecordsTable({
  records,
  isLoading = false,
  onEdit,
  onDelete,
  onRemoveLink,
  onAddEntity,
  entities = [],
  isLoadingEntities = false,
}: EntityRecordsTableProps) {
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([])

  const columns: ColumnDef<CaseRecordLinkRead>[] = [
    {
      accessorKey: "created_at",
      header: () => <span className="text-xs">Created</span>,
      cell: ({ row }) => {
        const createdAt = row.original.created_at
        if (!createdAt) {
          return <span className="text-xs text-muted-foreground">-</span>
        }
        try {
          const date = new Date(createdAt)
          return (
            <span className="text-xs text-muted-foreground">
              {shortTimeAgo(date)}
            </span>
          )
        } catch {
          return <span className="text-xs text-muted-foreground">-</span>
        }
      },
      size: 100,
      minSize: 100,
      maxSize: 100,
    },
    {
      accessorKey: "updated_at",
      header: () => <span className="text-xs">Updated</span>,
      cell: ({ row }) => {
        // Prefer the record's updated_at if available; fall back to link's
        const getRecordUpdatedAt = (
          rec: CaseRecordLinkRead["record"]
        ): string | undefined => {
          if (!rec || typeof rec !== "object") return undefined
          if ("updated_at" in rec) {
            const v = (rec as { updated_at?: unknown }).updated_at
            return typeof v === "string" ? v : undefined
          }
          return undefined
        }
        const recordUpdated = getRecordUpdatedAt(row.original.record)
        const updatedAt = recordUpdated ?? row.original.updated_at
        if (!updatedAt) {
          return <span className="text-xs text-muted-foreground">-</span>
        }
        try {
          const date = new Date(updatedAt)
          return (
            <span className="text-xs text-muted-foreground">
              {shortTimeAgo(date)}
            </span>
          )
        } catch {
          return <span className="text-xs text-muted-foreground">-</span>
        }
      },
      size: 100,
      minSize: 100,
      maxSize: 100,
    },
    {
      id: "entity",
      accessorFn: (row) =>
        row.entity?.display_name || row.entity?.name || "Unknown",
      header: () => <span className="text-xs">Entity</span>,
      cell: ({ row }) => {
        const entity = row.original.entity
        if (!entity) {
          return <span className="text-xs text-muted-foreground">Unknown</span>
        }

        const Icon = entity.icon ? getIconByName(entity.icon) : null

        return (
          <div className="flex items-center gap-2">
            <Avatar className="size-5">
              <AvatarFallback className="text-xs">
                {Icon ? (
                  <Icon className="size-3" />
                ) : (
                  (
                    entity.display_name?.[0] ||
                    entity.name?.[0] ||
                    "?"
                  ).toUpperCase()
                )}
              </AvatarFallback>
            </Avatar>
            <span className="text-xs font-medium">
              {entity.display_name || entity.name}
            </span>
          </div>
        )
      },
      filterFn: (row, id, value: string[]) => {
        const entityName = row.getValue(id) as string
        return value.includes(entityName)
      },
      enableSorting: true,
      enableHiding: false,
      enableColumnFilter: true,
    },
    {
      accessorKey: "record",
      header: () => <span className="text-xs">Record</span>,
      cell: ({ row }) => {
        const record = row.original.record
        if (!record || !record.field_data) {
          return <span className="text-xs text-muted-foreground">No data</span>
        }

        // Check if field_data is empty
        const fieldData = record.field_data
        if (Object.keys(fieldData).length === 0) {
          return (
            <span className="text-xs text-muted-foreground">No fields</span>
          )
        }

        return (
          <div className="overflow-auto w-64">
            <JsonView
              collapsed={false}
              displaySize
              enableClipboard
              src={fieldData}
              className="break-all text-xs"
              theme="atom"
              collapseStringsAfterLength={50}
            />
          </div>
        )
      },
    },
    {
      id: "actions",
      header: () => <span className="sr-only">Actions</span>,
      enableSorting: false,
      enableHiding: false,
      size: 32,
      minSize: 32,
      maxSize: 32,
      cell: ({ row }) => {
        const recordLink = row.original

        return (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" className="size-8 p-0">
                <span className="sr-only">Open menu</span>
                <DotsHorizontalIcon className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {onEdit && (
                <DropdownMenuItem
                  className="flex cursor-pointer items-center gap-2 text-xs"
                  onClick={() => onEdit(recordLink)}
                >
                  <Edit className="size-3" />
                  Edit
                </DropdownMenuItem>
              )}
              {onRemoveLink && (
                <DropdownMenuItem
                  className="flex cursor-pointer items-center gap-2 text-xs"
                  onClick={() => onRemoveLink(recordLink)}
                >
                  <Unlink className="size-3" />
                  Unlink from case
                </DropdownMenuItem>
              )}
              {onDelete && (
                <DropdownMenuItem
                  className="flex cursor-pointer items-center gap-2 text-xs text-destructive focus:text-destructive"
                  onClick={() => onDelete(recordLink)}
                >
                  <Trash2 className="size-3" />
                  Delete
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        )
      },
    },
  ]

  // Get unique entity options for filtering
  const entityOptions = useMemo(() => {
    const uniqueEntities = new Set<string>()
    records.forEach((record) => {
      if (record.entity) {
        const entityName = record.entity.display_name || record.entity.name
        uniqueEntities.add(entityName)
      }
    })
    return Array.from(uniqueEntities).map((entity) => ({
      label: entity,
      value: entity,
    }))
  }, [records])

  const table = useReactTable({
    data: records,
    columns,
    state: {
      columnFilters,
    },
    onColumnFiltersChange: setColumnFilters,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getFacetedRowModel: getFacetedRowModel(),
    getFacetedUniqueValues: getFacetedUniqueValues(),
  })

  const isFiltered = table.getState().columnFilters.length > 0

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {entityOptions.length > 1 && (
            <>
              <DataTableFacetedFilter
                column={table.getColumn("entity")}
                title="Entity"
                options={entityOptions}
              />
              {isFiltered && (
                <Button
                  variant="ghost"
                  onClick={() => table.resetColumnFilters()}
                  className="h-8 px-2 text-xs"
                >
                  Reset
                </Button>
              )}
            </>
          )}
        </div>
        {onAddEntity && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm" className="h-8 text-xs">
                <Plus className="mr-2 h-3.5 w-3.5" />
                Add entity record
                <ChevronDown className="ml-2 h-3.5 w-3.5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-[200px]">
              {isLoadingEntities ? (
                <DropdownMenuItem disabled>
                  <Loader2 className="mr-2 h-3 w-3 animate-spin" />
                  Loading entities...
                </DropdownMenuItem>
              ) : entities && entities.length > 0 ? (
                entities.map((entity) => {
                  const Icon = entity.icon ? getIconByName(entity.icon) : null
                  return (
                    <DropdownMenuItem
                      key={entity.id}
                      onClick={() => onAddEntity(entity.id)}
                      className="text-xs"
                    >
                      {Icon ? (
                        <Icon className="mr-2 h-3 w-3" />
                      ) : (
                        <Database className="mr-2 h-3 w-3" />
                      )}
                      {entity.display_name}
                    </DropdownMenuItem>
                  )
                })
              ) : (
                <DropdownMenuItem disabled className="text-xs">
                  No entities available
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow key={headerGroup.id}>
                {headerGroup.headers.map((header) => {
                  return (
                    <TableHead
                      key={header.id}
                      className={
                        header.column.id === "actions" ? "text-right" : ""
                      }
                    >
                      {header.isPlaceholder
                        ? null
                        : flexRender(
                            header.column.columnDef.header,
                            header.getContext()
                          )}
                    </TableHead>
                  )
                })}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {isLoading ? (
              // Show skeleton rows while loading
              Array.from({ length: 3 }).map((_, index) => (
                <TableRow key={`skeleton-${index}`}>
                  {columns.map((_, cellIndex) => (
                    <TableCell key={`skeleton-cell-${cellIndex}`}>
                      <Skeleton className="h-4 w-full" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : table.getRowModel().rows?.length ? (
              table.getRowModel().rows.map((row) => (
                <TableRow
                  key={row.id}
                  data-state={row.getIsSelected() && "selected"}
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell
                      key={cell.id}
                      className={
                        cell.column.id === "actions" ? "text-right" : ""
                      }
                    >
                      {flexRender(
                        cell.column.columnDef.cell,
                        cell.getContext()
                      )}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell
                  colSpan={columns.length}
                  className="h-24 text-center"
                >
                  <div className="flex flex-col items-center justify-center text-muted-foreground">
                    <p className="text-xs">No entity records found</p>
                  </div>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
