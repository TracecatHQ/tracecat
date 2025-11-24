"use client"

import type { CellContext, Column, ColumnDef } from "@tanstack/react-table"
import { format } from "date-fns"
import { DatabaseZapIcon } from "lucide-react"
import React, { useEffect, useState } from "react"
import type { TableColumnRead, TableRead, TableRowRead } from "@/client"
import { DataTable, SimpleColumnHeader } from "@/components/data-table"
import { SqlTypeBadge } from "@/components/data-type/sql-type-display"
import { JsonViewWithControls } from "@/components/json-viewer"
import { TableViewAction } from "@/components/tables/table-view-action"
import { TableViewColumnMenu } from "@/components/tables/table-view-column-menu"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useTablesPagination } from "@/hooks/pagination/use-tables-pagination"
import type { SqlType } from "@/lib/data-type"
import { useWorkspaceId } from "@/providers/workspace-id"

const TEXT_TYPES = new Set([
  "TEXT",
  "VARCHAR",
  "CHAR",
  "CITEXT",
  "UUID",
  "BPCHAR",
])
const JSON_TYPES = new Set(["JSON", "JSONB"])
const NUMERIC_TYPES = new Set([
  "INT",
  "INTEGER",
  "BIGINT",
  "SMALLINT",
  "DECIMAL",
  "NUMERIC",
  "REAL",
  "FLOAT",
  "FLOAT8",
  "FLOAT4",
  "DOUBLE",
  "DOUBLE PRECISION",
  "BIGSERIAL",
  "SERIAL",
  "SERIAL4",
  "SERIAL8",
])
const DATE_TYPES = new Set([
  "DATE",
  "TIMESTAMP",
  "TIMESTAMPTZ",
  "TIME",
  "TIMETZ",
])
const BOOLEAN_TYPES = new Set(["BOOL", "BOOLEAN"])
const TIMESTAMP_TYPES = new Set(["TIMESTAMP", "TIMESTAMPTZ"])

const normalizeSqlType = (rawType?: string) => {
  if (!rawType) return ""
  const [base] = rawType.toUpperCase().split("(")
  return base.trim()
}

const getColumnWidth = (rawType?: string) => {
  const normalizedType = normalizeSqlType(rawType)
  if (JSON_TYPES.has(normalizedType)) return "30rem"
  if (TEXT_TYPES.has(normalizedType)) return "24rem"
  if (DATE_TYPES.has(normalizedType)) return "18rem"
  if (BOOLEAN_TYPES.has(normalizedType)) return "10rem"
  if (NUMERIC_TYPES.has(normalizedType)) return "14rem"
  return "18rem"
}

const sanitizeColumnOptions = (options?: Array<string> | null) => {
  if (!Array.isArray(options)) {
    return undefined
  }
  const normalized = options
    .map((option) => (typeof option === "string" ? option.trim() : ""))
    .filter((option): option is string => option.length > 0)
  return normalized.length > 0 ? normalized : undefined
}

function CollapsibleText({ text }: { text: string }) {
  const [isExpanded, setIsExpanded] = React.useState(false)
  const containerRef = React.useRef<HTMLDivElement>(null)
  const [containerWidth, setContainerWidth] = React.useState(0)

  // Measure container width on mount and when window resizes
  React.useEffect(() => {
    const updateWidth = () => {
      if (containerRef.current) {
        setContainerWidth(containerRef.current.clientWidth)
      }
    }

    updateWidth()
    window.addEventListener("resize", updateWidth)
    return () => window.removeEventListener("resize", updateWidth)
  }, [])

  // Estimate characters per line based on container width (approximate for default font)
  const charsPerLine = Math.max(25, Math.floor(containerWidth / 7))

  if (!isExpanded) {
    return (
      <div ref={containerRef} className="flex w-full items-center gap-1">
        <span className="truncate text-xs font-sans">
          {text.substring(0, charsPerLine)}
        </span>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setIsExpanded(true)}
          className="h-6 px-1 text-xs font-sans text-muted-foreground hover:bg-transparent"
        >
          ...
        </Button>
      </div>
    )
  }

  return (
    <div ref={containerRef} className="w-full space-y-1 text-xs font-sans">
      <p className="whitespace-pre-wrap break-words leading-relaxed">{text}</p>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setIsExpanded(false)}
        className="h-6 px-2 text-xs font-sans text-muted-foreground hover:bg-transparent"
      >
        Collapse
      </Button>
    </div>
  )
}

export function DatabaseTable({
  table: { id, name, columns },
}: {
  table: TableRead
}) {
  const workspaceId = useWorkspaceId()
  const [pageSize, setPageSize] = useState(20)

  const {
    data: rows,
    isLoading: rowsIsLoading,
    error: rowsError,
    goToNextPage,
    goToPreviousPage,
    goToFirstPage,
    hasNextPage,
    hasPreviousPage,
    currentPage,
    totalEstimate,
    startItem,
    endItem,
  } = useTablesPagination({
    tableId: id,
    workspaceId,
    limit: pageSize,
  })

  useEffect(() => {
    if (id) {
      document.title = `Tables | ${name}`
    }
  }, [id, name])

  const handlePageSizeChange = (newPageSize: number) => {
    setPageSize(newPageSize)
    goToFirstPage()
  }

  type CellT = CellContext<TableRowRead, TableColumnRead>
  const allColumns: ColumnDef<TableRowRead, TableColumnRead>[] = [
    ...columns.map((column) => {
      const normalizedType = normalizeSqlType(column.type)
      const columnWidth = getColumnWidth(column.type)
      const widthStyle: React.CSSProperties = {
        width: columnWidth,
        minWidth: columnWidth,
        maxWidth: columnWidth,
      }

      return {
        accessorKey: column.name,
        header: ({
          column: tableColumn,
        }: {
          column: Column<TableRowRead, unknown>
        }) => (
          <div className="flex items-center gap-2">
            <SimpleColumnHeader
              column={tableColumn}
              title={column.name}
              className="text-xs"
            />
            <SqlTypeBadge type={column.type as SqlType} />
            {column.is_index && (
              <span className="inline-flex items-center rounded-full bg-green-100 px-1.5 py-0.5 text-xs font-medium text-green-800 dark:bg-green-900 dark:text-green-100">
                <DatabaseZapIcon className="mr-1 size-3" />
                Index
              </span>
            )}
            <TableViewColumnMenu column={column} />
          </div>
        ),
        cell: ({ row }: CellT) => {
          const value = row.original[column.name as keyof TableRowRead]
          const isDateLike =
            normalizedType === "DATE" || TIMESTAMP_TYPES.has(normalizedType)
          const isSelectColumn = normalizedType === "SELECT"
          const isMultiSelectColumn = normalizedType === "MULTI_SELECT"
          const columnOptions = sanitizeColumnOptions(column.options)
          const parsedMultiValue = Array.isArray(value)
            ? value.filter((item): item is string => typeof item === "string")
            : typeof value === "string" && value.length > 0
              ? [value]
              : []
          const multiSelectValues =
            columnOptions && columnOptions.length > 0
              ? parsedMultiValue.filter((item) => columnOptions.includes(item))
              : parsedMultiValue

          // For DATE types, parse without timezone conversion
          // For TIMESTAMP types, use standard Date parsing
          let parsedDate: Date | undefined
          if (isDateLike && typeof value === "string" && value) {
            if (normalizedType === "DATE") {
              const [year, month, day] = value.split("-").map(Number)
              parsedDate = new Date(year, month - 1, day)
            } else {
              parsedDate = new Date(value)
            }
          }

          const isValidDate =
            parsedDate && !Number.isNaN(parsedDate.getTime())
              ? parsedDate
              : null
          const selectDisplayValue =
            typeof value === "string"
              ? value
              : value === null || value === undefined
                ? ""
                : String(value)

          return (
            <div className="w-full text-xs font-sans text-foreground">
              {isMultiSelectColumn ? (
                multiSelectValues.length > 0 ? (
                  <div className="flex flex-wrap gap-1">
                    {multiSelectValues.map((item, idx) => (
                      <Badge
                        key={`${column.id}-${item}-${idx}`}
                        variant="secondary"
                        className="text-[11px]"
                      >
                        {item}
                      </Badge>
                    ))}
                  </div>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )
              ) : isSelectColumn ? (
                <span className="whitespace-pre-wrap break-words">
                  {selectDisplayValue || "—"}
                </span>
              ) : typeof value === "object" && value ? (
                <button
                  type="button"
                  onClick={(e) => e.stopPropagation()}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.stopPropagation()
                    }
                  }}
                  className="w-full cursor-default text-left"
                >
                  <TooltipProvider>
                    <JsonViewWithControls src={value} />
                  </TooltipProvider>
                </button>
              ) : isValidDate ? (
                <span>
                  {format(
                    isValidDate,
                    normalizedType === "DATE"
                      ? "MMM d yyyy"
                      : "MMM d yyyy '·' p"
                  )}
                </span>
              ) : typeof value === "string" && value.length > 25 ? (
                <CollapsibleText text={String(value)} />
              ) : (
                <span className="whitespace-pre-wrap break-words">
                  {String(value)}
                </span>
              )}
            </div>
          )
        },
        enableSorting: true,
        enableHiding: true,
        meta: {
          headerClassName: "px-3 text-left align-middle",
          cellClassName: "align-top px-3",
          headerStyle: widthStyle,
          cellStyle: widthStyle,
        },
      }
    }),
    {
      id: "actions",
      enableSorting: false,
      enableHiding: false,
      cell: ({ row }: CellT) => <TableViewAction row={row} />,
      meta: {
        headerClassName: "w-16 px-2 text-right",
        cellClassName: "w-16 px-2",
        headerStyle: { width: "4rem", minWidth: "4rem", maxWidth: "4rem" },
        cellStyle: { width: "4rem", minWidth: "4rem", maxWidth: "4rem" },
      },
    },
  ]

  return (
    <DataTable<TableRowRead, TableColumnRead>
      isLoading={rowsIsLoading}
      error={rowsError ?? undefined}
      data={rows}
      emptyMessage="No rows found."
      errorMessage="Error loading rows."
      columns={allColumns}
      serverSidePagination={{
        currentPage,
        hasNextPage,
        hasPreviousPage,
        pageSize,
        totalEstimate,
        startItem,
        endItem,
        onNextPage: goToNextPage,
        onPreviousPage: goToPreviousPage,
        onFirstPage: goToFirstPage,
        onPageSizeChange: handlePageSizeChange,
        isLoading: rowsIsLoading,
      }}
    />
  )
}
