"use client"

import { Plus, Unlink2 } from "lucide-react"
import { type ReactNode, useMemo, useState } from "react"
import type { TableRowRead } from "@/client"
import { CaseLinkRowsDialog } from "@/components/cases/case-link-rows-dialog"
import {
  CASE_PANEL_BOX_CLASS,
  CASE_TASK_ROW_CLASS,
} from "@/components/cases/case-task-fields"
import { Spinner } from "@/components/loading/spinner"
import { AgGridPagination } from "@/components/tables/ag-grid-pagination"
import { TableRowsGrid } from "@/components/tables/table-rows-grid"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { toast } from "@/components/ui/use-toast"
import { useCaseRowsPagination } from "@/hooks/pagination/use-case-rows-pagination"
import { useCaseLinkedTables, useUnlinkCaseRows } from "@/hooks/use-case-rows"
import { toGridRow, UNAVAILABLE_ROW_CLASS_RULES } from "@/lib/cases/case-rows"
import { getApiErrorDetail } from "@/lib/errors"
import { useGetTable } from "@/lib/hooks"
import { cn } from "@/lib/utils"

const DEFAULT_PAGE_SIZE = 20
const EMPTY_SELECTION: ReadonlySet<string> = new Set()
const EMPTY_ROWS: readonly TableRowRead[] = []

/** Props for {@link CaseLinkedRowsSection}. */
export interface CaseLinkedRowsSectionProps {
  caseId: string
  workspaceId: string
}

/**
 * The case's Tables panel: one paged grid per table with rows linked to the
 * case, each with its own selection for unlinking, and a ghost row that opens
 * the link dialog. The ghost row doubles as the empty state.
 */
export function CaseLinkedRowsSection({
  caseId,
  workspaceId,
}: CaseLinkedRowsSectionProps) {
  const { linkedTables, linkedTablesIsLoading, linkedTablesError } =
    useCaseLinkedTables({ caseId, workspaceId })
  const [linkDialogOpen, setLinkDialogOpen] = useState(false)
  const [linkDialogTableId, setLinkDialogTableId] = useState<string>()

  function openDialog(tableId?: string) {
    setLinkDialogTableId(tableId)
    setLinkDialogOpen(true)
  }

  if (linkedTablesIsLoading) {
    return (
      <div className={CASE_PANEL_BOX_CLASS}>
        {[...Array(3)].map((_, index) => (
          <Skeleton key={index} className="mx-2 my-1 h-7 rounded-md" />
        ))}
      </div>
    )
  }

  if (linkedTablesError) {
    return (
      <div className={CASE_PANEL_BOX_CLASS}>
        <p className="px-3 py-2 text-sm text-muted-foreground">
          Failed to load linked rows
        </p>
      </div>
    )
  }

  return (
    <>
      <div className={cn(CASE_PANEL_BOX_CLASS, "flex flex-col gap-4")}>
        {linkedTables.map((linkedTable) => (
          <CaseLinkedTableSection
            key={linkedTable.table_id}
            caseId={caseId}
            workspaceId={workspaceId}
            tableId={linkedTable.table_id}
            tableName={linkedTable.table_name ?? null}
            rowCount={linkedTable.row_count}
            onAddRows={() => openDialog(linkedTable.table_id)}
          />
        ))}
        <LinkRowsRow onClick={() => openDialog()} />
      </div>
      <CaseLinkRowsDialog
        open={linkDialogOpen}
        onOpenChange={setLinkDialogOpen}
        caseId={caseId}
        workspaceId={workspaceId}
        initialTableId={linkDialogTableId}
      />
    </>
  )
}

interface LinkRowsRowProps {
  onClick: () => void
}

/**
 * Muted ghost row that opens the link dialog. Built to the task row's
 * geometry, like the attachments panel's `+ Add attachment` row, and the
 * panel's only empty state.
 */
function LinkRowsRow({ onClick }: LinkRowsRowProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        CASE_TASK_ROW_CLASS,
        "flex h-11 w-full items-center gap-2 text-sm font-medium text-muted-foreground hover:bg-muted/50 hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
      )}
    >
      <span className="flex size-6 shrink-0 items-center justify-center">
        <Plus className="size-5" />
      </span>
      Link rows
    </button>
  )
}

interface CaseLinkedTableSectionProps {
  caseId: string
  workspaceId: string
  tableId: string
  tableName: string | null
  rowCount: number
  onAddRows: () => void
}

function CaseLinkedTableSection({
  caseId,
  workspaceId,
  tableId,
  tableName,
  rowCount,
  onAddRows,
}: CaseLinkedTableSectionProps) {
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  const [selectedRowIds, setSelectedRowIds] =
    useState<ReadonlySet<string>>(EMPTY_SELECTION)

  const { table, tableIsLoading, tableError } = useGetTable({
    tableId,
    workspaceId,
  })
  const {
    data: caseRows,
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
  } = useCaseRowsPagination({ caseId, tableId, workspaceId, limit: pageSize })
  const { unlinkCaseRows, unlinkCaseRowsIsPending } = useUnlinkCaseRows({
    caseId,
    workspaceId,
  })

  const rows = useMemo<readonly TableRowRead[]>(
    () => (caseRows.length > 0 ? caseRows.map(toGridRow) : EMPTY_ROWS),
    [caseRows]
  )
  const selectedCount = selectedRowIds.size

  function handlePageSizeChange(size: number) {
    setPageSize(size)
    goToFirstPage()
  }

  async function handleUnlink() {
    const rowIds = [...selectedRowIds]
    if (rowIds.length === 0) return
    try {
      const { unlinkedCount } = await unlinkCaseRows({ tableId, rowIds })
      setSelectedRowIds(EMPTY_SELECTION)
      goToFirstPage()
      toast({
        title: "Rows unlinked",
        description: `Unlinked ${unlinkedCount} ${
          unlinkedCount === 1 ? "row" : "rows"
        } from this case.`,
      })
    } catch (error) {
      toast({
        title: "Could not unlink rows",
        description: getApiErrorDetail(error) ?? "Try again.",
        variant: "destructive",
      })
    }
  }

  let gridContent: ReactNode
  if (tableIsLoading) {
    gridContent = (
      <div className="flex h-20 items-center justify-center">
        <Spinner className="size-4" />
      </div>
    )
  } else if (tableError || !table) {
    gridContent = (
      <div className="p-3 text-sm text-destructive">
        Failed to load table schema.
      </div>
    )
  } else if (rowsError) {
    gridContent = (
      <div className="p-3 text-sm text-destructive">
        Failed to load linked rows.
      </div>
    )
  } else {
    gridContent = (
      <TableRowsGrid
        columns={table.columns}
        rows={rows}
        tableId={tableId}
        isLoading={rowsIsLoading}
        selectable
        selectedRowIds={selectedRowIds}
        onSelectedRowIdsChange={(ids) => setSelectedRowIds(new Set(ids))}
        autoHeight
        rowClassRules={UNAVAILABLE_ROW_CLASS_RULES}
        widthScope="case-rows"
      />
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between px-3 py-1.5">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-medium">{tableName ?? "Table"}</span>
          <span className="text-xs text-muted-foreground">
            {rowCount} {rowCount === 1 ? "row" : "rows"}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {selectedCount > 0 && (
            <>
              <span className="text-xs text-muted-foreground tabular-nums">
                {selectedCount} selected
              </span>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-xs text-destructive hover:text-destructive"
                disabled={unlinkCaseRowsIsPending}
                onClick={handleUnlink}
              >
                {unlinkCaseRowsIsPending ? (
                  <Spinner className="mr-1 size-3" />
                ) : (
                  <Unlink2 className="mr-1 size-3" />
                )}
                Unlink
              </Button>
            </>
          )}
          <Button
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs text-muted-foreground"
            onClick={onAddRows}
          >
            <Plus className="mr-1 size-3" />
            Add rows
          </Button>
        </div>
      </div>
      <div className="overflow-x-auto rounded-md border">
        <div className="min-w-[1200px]">{gridContent}</div>
      </div>
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
  )
}
