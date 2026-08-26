"use client"

import { ChevronLeft, ChevronRight, Link2, Plus, Unlink2 } from "lucide-react"
import { type ReactNode, useMemo, useState } from "react"
import type { TableColumnRead, TableRowRead } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CaseLinkRowsDialog } from "@/components/cases/case-link-rows-dialog"
import {
  CASE_PANEL_ACTION_BOX_CLASS,
  CASE_PANEL_ACTION_ROW_CLASS,
  CASE_PANEL_BOX_CLASS,
  TASK_ICON_TRIGGER_CLASS,
} from "@/components/cases/case-task-fields"
import { Spinner } from "@/components/loading/spinner"
import { TableRowsGrid } from "@/components/tables/table-rows-grid"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { toast } from "@/components/ui/use-toast"
import { useCaseRowsPagination } from "@/hooks/pagination/use-case-rows-pagination"
import {
  CaseRowsUnlinkError,
  useCaseLinkedTables,
  useUnlinkCaseRows,
} from "@/hooks/use-case-rows"
import { toGridRow, UNAVAILABLE_ROW_CLASS_RULES } from "@/lib/cases/case-rows"
import { getApiErrorDetail } from "@/lib/errors"
import { cn } from "@/lib/utils"

/** Rows per page, fixed: the case view pages with two header arrows, not a bar. */
const PAGE_SIZE = 20
const EMPTY_SELECTION: ReadonlySet<string> = new Set()
const EMPTY_ROWS: readonly TableRowRead[] = []

/**
 * A header page arrow: the panel's shared 24px icon trigger, muted until
 * hovered and faded out at the ends of the range.
 */
const PAGE_ARROW_CLASS = cn(
  TASK_ICON_TRIGGER_CLASS,
  "text-muted-foreground hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
)

/** Props for {@link CaseLinkedRowsSection}. */
export interface CaseLinkedRowsSectionProps {
  caseId: string
  workspaceId: string
}

/**
 * The case's Tables panel: one grid per table with rows linked to the case,
 * each with its own selection for unlinking, and a compact action bar beneath
 * them that opens the link dialog. The action bar doubles as the empty state.
 * Rows page {@link PAGE_SIZE} at a time behind two arrows in each table's
 * header, so the panel carries no pagination bar.
 *
 * Column definitions ride along on the case-scoped linked-tables summary, so
 * viewing and unlinking need no `table:read`. Every mutation here is guarded
 * by `case:update` on the API, so the select and unlink controls only render
 * with that scope. The link and add controls additionally need `table:read`,
 * because the link dialog reads tables. Without those the grids stay read-only
 * and the empty state is plain text.
 */
export function CaseLinkedRowsSection({
  caseId,
  workspaceId,
}: CaseLinkedRowsSectionProps) {
  const { linkedTables, linkedTablesIsLoading, linkedTablesError } =
    useCaseLinkedTables({ caseId, workspaceId })
  // Scopes still loading reads as "not permitted", so controls never flash.
  const canUpdate = useScopeCheck("case:update") === true
  // The link dialog lists tables, loads a schema and pages rows behind `table:read`.
  const canLink =
    useScopeCheck("case:update", ["table:read"], { all: true }) === true
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
      <div className="flex flex-col gap-6">
        {linkedTables.map((linkedTable) => (
          <CaseLinkedTableSection
            key={linkedTable.table_id}
            caseId={caseId}
            workspaceId={workspaceId}
            tableId={linkedTable.table_id}
            tableName={linkedTable.table_name ?? null}
            rowCount={linkedTable.row_count}
            columns={linkedTable.columns}
            canUpdate={canUpdate}
            canLink={canLink}
            onAddRows={() => openDialog(linkedTable.table_id)}
          />
        ))}
        {canLink && (
          <div className={CASE_PANEL_ACTION_BOX_CLASS}>
            <LinkTableRow onClick={() => openDialog()} />
          </div>
        )}
        {!canLink && linkedTables.length === 0 && (
          <div className={CASE_PANEL_ACTION_BOX_CLASS}>
            <p
              className={cn(
                CASE_PANEL_ACTION_ROW_CLASS,
                "flex items-center text-sm text-muted-foreground"
              )}
            >
              No linked rows
            </p>
          </div>
        )}
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

interface LinkTableRowProps {
  onClick: () => void
}

/**
 * Muted ghost row that opens the link dialog. A compact action bar below the
 * tables rather than a full task row: one line of text, boxed on its own,
 * sharing its geometry with the attachments panel's empty state.
 */
function LinkTableRow({ onClick }: LinkTableRowProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        CASE_PANEL_ACTION_ROW_CLASS,
        "flex w-full items-center gap-2 text-sm font-medium text-muted-foreground hover:bg-muted/50 hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
      )}
    >
      <span className="flex size-6 shrink-0 items-center justify-center">
        <Link2 className="size-4" />
      </span>
      Link table
    </button>
  )
}

interface CaseLinkedTableSectionProps {
  caseId: string
  workspaceId: string
  tableId: string
  tableName: string | null
  rowCount: number
  /** Table schema from the case-scoped summary, not a `table:read`. */
  columns: readonly TableColumnRead[]
  /** Whether the viewer holds `case:update`; gates row selection and unlink. */
  canUpdate: boolean
  /**
   * Whether the viewer holds both `case:update` and `table:read`; gates the
   * add button, since the link dialog reads tables.
   */
  canLink: boolean
  onAddRows: () => void
}

/**
 * One linked table: a header line and its grid. The header carries the table's
 * name and row count on the left, and on the right the selection's unlink
 * control, the add button, and — only once the rows outrun a single page — two
 * borderless arrows. Paging is read-only, so the arrows ignore the scopes; the
 * count text becomes the visible range while paged, standing in for the page
 * number the arrows deliberately drop.
 */
function CaseLinkedTableSection({
  caseId,
  workspaceId,
  tableId,
  tableName,
  rowCount,
  columns,
  canUpdate,
  canLink,
  onAddRows,
}: CaseLinkedTableSectionProps) {
  const [selectedRowIds, setSelectedRowIds] =
    useState<ReadonlySet<string>>(EMPTY_SELECTION)

  const {
    data: caseRows,
    isLoading: rowsIsLoading,
    error: rowsError,
    goToNextPage,
    goToPreviousPage,
    goToFirstPage,
    hasNextPage,
    hasPreviousPage,
    totalEstimate,
    startItem,
    endItem,
  } = useCaseRowsPagination({ caseId, tableId, workspaceId, limit: PAGE_SIZE })
  const { unlinkCaseRows, unlinkCaseRowsIsPending } = useUnlinkCaseRows({
    caseId,
    workspaceId,
  })

  const rows = useMemo<readonly TableRowRead[]>(
    () => (caseRows.length > 0 ? caseRows.map(toGridRow) : EMPTY_ROWS),
    [caseRows]
  )
  const selectedCount = selectedRowIds.size
  // One page of rows needs no arrows and no range: the count says it all.
  const isPaged = hasPreviousPage || hasNextPage
  const totalRows = totalEstimate ?? rowCount

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
      // Every request commits on its own: deselect what landed before a retry.
      if (error instanceof CaseRowsUnlinkError) {
        const committedRowIds = new Set(error.committedRowIds)
        setSelectedRowIds((previous) =>
          dropCommitted(previous, committedRowIds)
        )
        goToFirstPage()
        const detail = getApiErrorDetail(error.cause) ?? "Try again."
        if (error.unlinkedCount > 0) {
          toast({
            title: "Some rows were not unlinked",
            description: `Unlinked ${error.unlinkedCount} ${
              error.unlinkedCount === 1 ? "row" : "rows"
            } before a request failed. ${detail}`,
            variant: "destructive",
          })
          return
        }
        toast({
          title: "Could not unlink rows",
          description: detail,
          variant: "destructive",
        })
        return
      }
      toast({
        title: "Could not unlink rows",
        description: getApiErrorDetail(error) ?? "Try again.",
        variant: "destructive",
      })
    }
  }

  let gridContent: ReactNode
  if (rowsError) {
    gridContent = (
      <div className="p-3 text-sm text-destructive">
        Failed to load linked rows.
      </div>
    )
  } else {
    gridContent = (
      <TableRowsGrid
        columns={columns}
        rows={rows}
        tableId={tableId}
        isLoading={rowsIsLoading}
        selectable={canUpdate}
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
      <div className="flex items-center justify-between px-1 py-1.5">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-medium">{tableName ?? "Table"}</span>
          {isPaged ? (
            <span className="text-xs text-muted-foreground tabular-nums">
              {startItem}–{endItem} of {totalRows}
            </span>
          ) : (
            <span className="text-xs text-muted-foreground">
              {rowCount} {rowCount === 1 ? "row" : "rows"}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          {canUpdate && selectedCount > 0 && (
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
          {canLink && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs text-muted-foreground"
              onClick={onAddRows}
            >
              <Plus className="mr-1 size-3" />
              Add rows
            </Button>
          )}
          {isPaged && (
            <span className="flex items-center">
              <button
                type="button"
                aria-label="Previous page"
                className={PAGE_ARROW_CLASS}
                disabled={!hasPreviousPage || rowsIsLoading}
                onClick={goToPreviousPage}
              >
                <ChevronLeft className="size-4" />
              </button>
              <button
                type="button"
                aria-label="Next page"
                className={PAGE_ARROW_CLASS}
                disabled={!hasNextPage || rowsIsLoading}
                onClick={goToNextPage}
              >
                <ChevronRight className="size-4" />
              </button>
            </span>
          )}
        </div>
      </div>
      <div className="overflow-x-auto rounded-md border">
        <div className="min-w-[1200px]">{gridContent}</div>
      </div>
    </div>
  )
}

/**
 * The selection minus everything a failed multi-request unlink already
 * committed, so a retry only re-sends what is left.
 */
function dropCommitted(
  selected: ReadonlySet<string>,
  committedRowIds: ReadonlySet<string>
): ReadonlySet<string> {
  const remaining = new Set(
    [...selected].filter((rowId) => !committedRowIds.has(rowId))
  )
  return remaining.size === 0 ? EMPTY_SELECTION : remaining
}
