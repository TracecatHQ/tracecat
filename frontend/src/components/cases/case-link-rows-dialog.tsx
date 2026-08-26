"use client"

import { type ReactNode, useCallback, useMemo, useState } from "react"
import type { TableRowRead } from "@/client"
import { Spinner } from "@/components/loading/spinner"
import { AgGridPagination } from "@/components/tables/ag-grid-pagination"
import { TableRowsGrid } from "@/components/tables/table-rows-grid"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  nonDismissableDialogProps,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { toast } from "@/components/ui/use-toast"
import { useTablesPagination } from "@/hooks/pagination/use-tables-pagination"
import { useLinkCaseRows } from "@/hooks/use-case-rows"
import { getApiErrorDetail } from "@/lib/errors"
import { useGetTable, useListTables } from "@/lib/hooks"

const DEFAULT_PAGE_SIZE = 20
const EMPTY_SELECTION: ReadonlySet<string> = new Set()
const EMPTY_ROWS: readonly TableRowRead[] = []

/** Props for {@link CaseLinkRowsDialog}. */
export interface CaseLinkRowsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  caseId: string
  workspaceId: string
  /** Table preselected on open; falls back to the first table by name. */
  initialTableId?: string
}

/**
 * Picks rows to link to a case: choose a table, page through its rows, tick
 * the ones to add. Dismissable only via the close button.
 */
export function CaseLinkRowsDialog({
  open,
  onOpenChange,
  caseId,
  workspaceId,
  initialTableId,
}: CaseLinkRowsDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        {...nonDismissableDialogProps}
        title="Link rows"
        className="flex h-[min(92dvh,960px)] w-[min(96vw,1440px)] max-w-none flex-col gap-0 overflow-hidden p-0"
      >
        {/* Radix unmounts the body on close, so every pick resets for free. */}
        <CaseLinkRowsDialogBody
          caseId={caseId}
          workspaceId={workspaceId}
          initialTableId={initialTableId}
          onOpenChange={onOpenChange}
        />
      </DialogContent>
    </Dialog>
  )
}

interface CaseLinkRowsDialogBodyProps {
  caseId: string
  workspaceId: string
  initialTableId?: string
  onOpenChange: (open: boolean) => void
}

function CaseLinkRowsDialogBody({
  caseId,
  workspaceId,
  initialTableId,
  onOpenChange,
}: CaseLinkRowsDialogBodyProps) {
  const { tables, tablesIsLoading, tablesError } = useListTables({
    workspaceId,
  })
  const sortedTables = useMemo(
    () => [...(tables ?? [])].sort((a, b) => a.name.localeCompare(b.name)),
    [tables]
  )

  const [pickedTableId, setPickedTableId] = useState(initialTableId)
  const tableId = pickedTableId ?? sortedTables[0]?.id ?? ""
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  // Picks are kept per table so switching tables never drops a selection.
  const [stagedByTable, setStagedByTable] = useState<
    ReadonlyMap<string, ReadonlySet<string>>
  >(() => new Map())

  const { table, tableIsLoading, tableError } = useGetTable(
    { tableId, workspaceId },
    { enabled: Boolean(tableId) }
  )
  const {
    data: pageRows,
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
    tableId,
    workspaceId,
    limit: pageSize,
    enabled: Boolean(tableId),
  })
  const gridRows = pageRows.length > 0 ? pageRows : EMPTY_ROWS

  const { linkCaseRows, linkCaseRowsIsPending } = useLinkCaseRows({
    caseId,
    workspaceId,
  })

  const staged = stagedByTable.get(tableId) ?? EMPTY_SELECTION
  let totalStaged = 0
  let stagedTableCount = 0
  for (const rowIds of stagedByTable.values()) {
    if (rowIds.size === 0) continue
    totalStaged += rowIds.size
    stagedTableCount += 1
  }

  const handleStagedChange = useCallback(
    (rowIds: string[]) => {
      setStagedByTable((previous) => {
        const next = new Map(previous)
        if (rowIds.length === 0) {
          next.delete(tableId)
        } else {
          next.set(tableId, new Set(rowIds))
        }
        return next
      })
    },
    [tableId]
  )

  function handlePageSizeChange(size: number) {
    setPageSize(size)
    goToFirstPage()
  }

  function handleClear() {
    setStagedByTable(new Map())
  }

  async function handleAdd() {
    let linkedCount = 0
    let alreadyLinkedCount = 0
    try {
      for (const [stagedTableId, rowIds] of stagedByTable) {
        if (rowIds.size === 0) continue
        const result = await linkCaseRows({
          tableId: stagedTableId,
          rowIds: [...rowIds],
        })
        linkedCount += result.linkedCount
        alreadyLinkedCount += result.alreadyLinkedCount
      }
    } catch (error) {
      toast({
        title: "Could not link rows",
        description: getApiErrorDetail(error) ?? "Try again.",
        variant: "destructive",
      })
      return
    }
    toast({
      title: "Rows linked",
      description: describeLinkResult(linkedCount, alreadyLinkedCount),
    })
    onOpenChange(false)
  }

  let gridContent: ReactNode
  if (tablesError) {
    gridContent = <GridMessage tone="error">Failed to load tables.</GridMessage>
  } else if (!tablesIsLoading && sortedTables.length === 0) {
    gridContent = <GridMessage>No tables in this workspace</GridMessage>
  } else if (tableError || rowsError) {
    gridContent = (
      <GridMessage tone="error">Failed to load table rows.</GridMessage>
    )
  } else if (tableIsLoading || !table) {
    gridContent = (
      <div className="flex h-full items-center justify-center">
        <Spinner className="size-5" />
      </div>
    )
  } else {
    gridContent = (
      <TableRowsGrid
        key={tableId}
        columns={table.columns}
        rows={gridRows}
        tableId={tableId}
        isLoading={rowsIsLoading}
        selectable
        selectedRowIds={staged}
        onSelectedRowIdsChange={handleStagedChange}
        widthScope="case-link-rows"
      />
    )
  }

  let summary = `${totalStaged} selected`
  if (stagedTableCount > 1) {
    summary += ` across ${stagedTableCount} tables`
  }

  let addLabel = "Add rows"
  if (totalStaged === 1) {
    addLabel = "Add 1 row"
  } else if (totalStaged > 1) {
    addLabel = `Add ${totalStaged} rows`
  }

  return (
    <>
      <div className="flex flex-col gap-3 px-6 pt-6 pb-4 pr-10">
        <div className="flex flex-col gap-1.5">
          <DialogTitle>Link rows</DialogTitle>
          <DialogDescription>
            Select rows to link to this case.
          </DialogDescription>
        </div>
        <Select
          value={tableId}
          onValueChange={setPickedTableId}
          disabled={tablesIsLoading || sortedTables.length === 0}
        >
          <SelectTrigger className="h-8 w-[280px]" aria-label="Table">
            <SelectValue placeholder="Select a table" />
          </SelectTrigger>
          <SelectContent>
            {sortedTables.map((option) => (
              <SelectItem key={option.id} value={option.id}>
                {option.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="min-h-0 flex-1 border-y">{gridContent}</div>
      <div className="px-6 py-2">
        <AgGridPagination
          currentPage={currentPage}
          hasNextPage={hasNextPage}
          hasPreviousPage={hasPreviousPage}
          pageSize={pageSize}
          totalEstimate={totalEstimate}
          startItem={startItem}
          endItem={endItem}
          onNextPage={goToNextPage}
          onPreviousPage={goToPreviousPage}
          onFirstPage={goToFirstPage}
          onPageSizeChange={handlePageSizeChange}
          isLoading={rowsIsLoading}
        />
      </div>
      <div className="flex items-center justify-between border-t px-6 py-4">
        <p className="text-sm text-muted-foreground tabular-nums">{summary}</p>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            onClick={handleClear}
            disabled={totalStaged === 0 || linkCaseRowsIsPending}
          >
            Clear
          </Button>
          <Button
            onClick={handleAdd}
            disabled={totalStaged === 0 || linkCaseRowsIsPending}
          >
            {linkCaseRowsIsPending ? (
              <span className="flex items-center gap-2">
                <Spinner className="size-4" />
                Adding…
              </span>
            ) : (
              addLabel
            )}
          </Button>
        </div>
      </div>
    </>
  )
}

function describeLinkResult(
  linkedCount: number,
  alreadyLinkedCount: number
): string {
  const linkedNoun = linkedCount === 1 ? "row" : "rows"
  let description = `Linked ${linkedCount} ${linkedNoun} to this case.`
  if (alreadyLinkedCount === 1) {
    description += " 1 was already linked."
  } else if (alreadyLinkedCount > 1) {
    description += ` ${alreadyLinkedCount} were already linked.`
  }
  return description
}

interface GridMessageProps {
  tone?: "muted" | "error"
  children: ReactNode
}

function GridMessage({ tone = "muted", children }: GridMessageProps) {
  return (
    <div className="flex h-full items-center justify-center p-6">
      <p
        className={
          tone === "error"
            ? "text-sm text-destructive"
            : "text-sm text-muted-foreground"
        }
      >
        {children}
      </p>
    </div>
  )
}
