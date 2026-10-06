"use client"

import {
  ChevronLeft,
  ChevronRight,
  Link2,
  Maximize2,
  Plus,
  Unlink2,
  XIcon,
} from "lucide-react"
import { type ReactNode, useMemo, useState } from "react"
import type { TableColumnRead, TableRowRead } from "@/client"
import { CaseInsertRowDialog } from "@/components/cases/case-insert-row-dialog"
import { TASK_ICON_TRIGGER_CLASS } from "@/components/cases/case-task-fields"
import { Spinner } from "@/components/loading/spinner"
import { AgGridPagination } from "@/components/tables/ag-grid-pagination"
import {
  TABLE_PANEL_TITLES,
  TablePanelProvider,
  useTablePanel,
} from "@/components/tables/table-panel-context"
import {
  type TableRowCellChange,
  TableRowsGrid,
} from "@/components/tables/table-rows-grid"
import { TableSidePanelContent } from "@/components/tables/table-side-panel"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { useCaseRowsPagination } from "@/hooks/pagination/use-case-rows-pagination"
import {
  CaseRowsUnlinkError,
  useUnlinkCaseRows,
  useUpdateCaseRow,
} from "@/hooks/use-case-rows"
import {
  isAvailableRow,
  toGridRow,
  UNAVAILABLE_ROW_CLASS_RULES,
} from "@/lib/cases/case-rows"
import { getApiErrorDetail } from "@/lib/errors"
import { cn } from "@/lib/utils"

/**
 * Rows per page inline, fixed: the case view pages with two header arrows, not
 * a bar. The expanded dialog starts from the same size and lets it change.
 */
export const CASE_LINKED_TABLE_PAGE_SIZE = 20
const EMPTY_SELECTION: ReadonlySet<string> = new Set()
const EMPTY_ROWS: readonly TableRowRead[] = []

/** The quiet ghost button every table header action shares. */
const HEADER_ACTION_CLASS = "h-7 px-2 text-xs text-muted-foreground"

/**
 * A header page arrow: the panel's shared 24px icon trigger, muted until
 * hovered and faded out at the ends of the range.
 */
const PAGE_ARROW_CLASS = cn(
  TASK_ICON_TRIGGER_CLASS,
  "text-muted-foreground hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
)

/** Props for {@link CaseLinkedTable}. */
export interface CaseLinkedTableProps {
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
   * link button, since the link dialog reads tables.
   */
  canLink: boolean
  /**
   * Whether the viewer holds both `case:update` and `table:create`; gates the
   * add button, which inserts a new row and links it.
   */
  canAddRow: boolean
  /** Whether the viewer holds `table:update`; gates editing cells. */
  canEditCells: boolean
  onLinkRows: () => void
  /**
   * `inline` is the table as it sits in the case's Tables panel. `expanded` is
   * the same table filling a dialog: its grid takes the height it is given,
   * a pagination bar replaces the header arrows, and long values open in a
   * pane beside the grid.
   */
  variant?: "inline" | "expanded"
}

/**
 * One linked table: a header line and its grid. The header carries the table's
 * name and row count on the left, and on the right the selection's unlink
 * control, the link button, the add button, the expand button, and — only once
 * the rows outrun a single page — two borderless arrows. Paging is read-only,
 * so the arrows ignore the scopes; the count text becomes the visible range
 * once a paged request lands, standing in for the page number the arrows
 * deliberately drop.
 *
 * The expand button opens the same component as its `expanded` variant in a
 * dialog. The two are separate instances, each with its own page and its own
 * selection; they agree through the query cache, which every mutation here
 * invalidates.
 *
 * Cells are edited in place. The grid holds an edit locally while it saves, so
 * once the save settles the rows are rebuilt from the refetched links: a
 * rejected edit falls back to the server's value instead of lingering.
 */
export function CaseLinkedTable({
  caseId,
  workspaceId,
  tableId,
  tableName,
  rowCount,
  columns,
  canUpdate,
  canLink,
  canAddRow,
  canEditCells,
  onLinkRows,
  variant = "inline",
}: CaseLinkedTableProps) {
  const isExpanded = variant === "expanded"
  const [selectedRowIds, setSelectedRowIds] =
    useState<ReadonlySet<string>>(EMPTY_SELECTION)
  const [insertDialogOpen, setInsertDialogOpen] = useState(false)
  const [expandedOpen, setExpandedOpen] = useState(false)
  const [pageSize, setPageSize] = useState(CASE_LINKED_TABLE_PAGE_SIZE)
  // Bumped after every save so the grid's rows are rebuilt from the cache.
  const [rowsRevision, setRowsRevision] = useState(0)

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
  const { updateCaseRow } = useUpdateCaseRow({ caseId, workspaceId })

  // A refetch that changes nothing hands back the same links, so `caseRows`
  // alone would leave the grid showing an edit the server refused; the
  // revision forces fresh row objects, which the grid takes as the truth.
  const rows = useMemo<readonly TableRowRead[]>(
    () => (caseRows.length > 0 ? caseRows.map(toGridRow) : EMPTY_ROWS),
    [caseRows, rowsRevision]
  )
  const selectedCount = selectedRowIds.size
  // One page of rows needs no arrows and no range: the count says it all.
  const isPaged = hasPreviousPage || hasNextPage
  // Stepping to a page reports the new page's bounds before its rows arrive, so
  // an in-flight or failed page would read backwards ("21–20 of 0"): fall back
  // to the summary count until the page has rows to describe.
  const showRange =
    isPaged &&
    !rowsIsLoading &&
    !rowsError &&
    caseRows.length > 0 &&
    endItem >= startItem
  // A missing or zero estimate is the empty page talking, not a real total.
  const totalRows =
    totalEstimate && totalEstimate > 0 ? totalEstimate : rowCount

  function handleExpand() {
    // The dialog keeps its own selection, and can unlink rows ticked here.
    setSelectedRowIds(EMPTY_SELECTION)
    setExpandedOpen(true)
  }

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

  async function handleCellValueChange({
    rowId,
    column,
    value,
  }: TableRowCellChange) {
    try {
      await updateCaseRow({ tableId, rowId, data: { [column]: value } })
    } catch {
      // The update hook already toasted; the rebuild below drops the edit.
    }
    setRowsRevision((revision) => revision + 1)
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
        autoHeight={!isExpanded}
        rowClassRules={UNAVAILABLE_ROW_CLASS_RULES}
        widthScope="case-rows"
        cellPanel
        onCellValueChange={canEditCells ? handleCellValueChange : undefined}
        isRowEditable={isAvailableRow}
        sizeColumnsToContent
      />
    )
  }

  const header = (
    <div
      className={cn(
        "flex items-center justify-between gap-4",
        // The dialog's close button sits in its top-right corner.
        isExpanded ? "px-6 pb-4 pr-14 pt-5" : "px-1 py-1.5"
      )}
    >
      <div className="flex min-w-0 items-baseline gap-2">
        <span className="truncate text-sm font-medium">
          {tableName ?? "Table"}
        </span>
        {showRange ? (
          <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
            {startItem}–{endItem} of {totalRows}
          </span>
        ) : (
          <span className="shrink-0 text-xs text-muted-foreground">
            {rowCount} {rowCount === 1 ? "row" : "rows"}
          </span>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-1">
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
            className={HEADER_ACTION_CLASS}
            onClick={onLinkRows}
          >
            <Link2 className="mr-1 size-3" />
            Link rows
          </Button>
        )}
        {canAddRow && (
          <Button
            variant="ghost"
            size="sm"
            className={HEADER_ACTION_CLASS}
            onClick={() => setInsertDialogOpen(true)}
          >
            <Plus className="mr-1 size-3" />
            Add row
          </Button>
        )}
        {!isExpanded && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                aria-label="Expand table"
                className="size-7 p-0 text-muted-foreground"
                onClick={handleExpand}
              >
                <Maximize2 className="size-3" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Expand table</TooltipContent>
          </Tooltip>
        )}
        {!isExpanded && isPaged && (
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
  )

  const insertDialog = canAddRow && (
    <CaseInsertRowDialog
      open={insertDialogOpen}
      onOpenChange={setInsertDialogOpen}
      caseId={caseId}
      workspaceId={workspaceId}
      tableId={tableId}
      tableName={tableName}
      columns={columns}
    />
  )

  if (isExpanded) {
    return (
      <>
        {header}
        <div className="flex min-h-0 flex-1 border-y">
          <div className="min-w-0 flex-1">{gridContent}</div>
          <CaseLinkedTableValuePane />
        </div>
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
        {insertDialog}
      </>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      {header}
      <div className="overflow-hidden rounded-md border">{gridContent}</div>
      {insertDialog}
      <CaseLinkedTableDialog
        open={expandedOpen}
        onOpenChange={setExpandedOpen}
        caseId={caseId}
        workspaceId={workspaceId}
        tableId={tableId}
        tableName={tableName}
        rowCount={rowCount}
        columns={columns}
        canUpdate={canUpdate}
        canLink={canLink}
        canAddRow={canAddRow}
        canEditCells={canEditCells}
        onLinkRows={onLinkRows}
      />
    </div>
  )
}

interface CaseLinkedTableDialogProps
  extends Omit<CaseLinkedTableProps, "variant"> {
  open: boolean
  onOpenChange: (open: boolean) => void
}

/**
 * A linked table at the size of the link rows dialog. It gets a cell panel of
 * its own: the page's value drawer sits outside the dialog, where a modal
 * dialog leaves it inert, so long values open in a pane inside it instead.
 */
function CaseLinkedTableDialog(props: CaseLinkedTableDialogProps) {
  return (
    <TablePanelProvider>
      <CaseLinkedTableDialogContent {...props} />
    </TablePanelProvider>
  )
}

function CaseLinkedTableDialogContent({
  open,
  onOpenChange,
  ...tableProps
}: CaseLinkedTableDialogProps) {
  const { panelOpen, closePanel } = useTablePanel()
  const title = tableProps.tableName ?? "Table"

  function handleOpenChange(nextOpen: boolean) {
    if (!nextOpen) closePanel()
    onOpenChange(nextOpen)
  }

  // A value open in the pane may hold an unsaved draft, so while it is open
  // nothing dismisses the dialog but its own close button.
  function keepOpenWithPane(event: Event) {
    if (panelOpen) event.preventDefault()
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        className="flex h-[min(92dvh,960px)] w-[min(96vw,1440px)] max-w-none flex-col gap-0 overflow-hidden p-0"
        onPointerDownOutside={keepOpenWithPane}
        onInteractOutside={keepOpenWithPane}
        onEscapeKeyDown={(event) => {
          // Radix sees Escape before the grid does: one that ends a cell edit
          // must not also close the dialog.
          const target = event.target instanceof Element ? event.target : null
          if (
            panelOpen ||
            target?.closest(".ag-cell-inline-editing, .ag-popup-editor")
          ) {
            event.preventDefault()
          }
        }}
      >
        <DialogTitle className="sr-only">{title}</DialogTitle>
        <DialogDescription className="sr-only">
          Rows of {title} linked to this case.
        </DialogDescription>
        {/* Radix unmounts the body on close, so the page and picks reset. */}
        <CaseLinkedTable {...tableProps} variant="expanded" />
      </DialogContent>
    </Dialog>
  )
}

/**
 * The expanded dialog's cell panel: a pane the grid shrinks next to, rather
 * than a drawer over it. Headed by the cell's column, with the mode
 * underneath, like the page's value drawer, and like it closed only by its
 * close button or the editor's own Save and Cancel.
 */
function CaseLinkedTableValuePane() {
  const { panelOpen, panelContent, closePanel } = useTablePanel()
  if (!panelOpen || !panelContent) return null
  const modeTitle = TABLE_PANEL_TITLES[panelContent.mode]

  return (
    <aside
      data-case-value-pane=""
      aria-label={panelContent.title ?? modeTitle}
      className="flex w-[min(32rem,45%)] shrink-0 flex-col border-l"
    >
      <div className="flex shrink-0 items-start justify-between gap-2 border-b px-4 py-3">
        <div className="min-w-0 space-y-1">
          <p className="truncate text-sm font-medium">
            {panelContent.title ?? modeTitle}
          </p>
          {panelContent.title && (
            <p className="text-xs text-muted-foreground">{modeTitle}</p>
          )}
        </div>
        <Button
          variant="ghost"
          size="sm"
          aria-label="Close panel"
          className="size-6 shrink-0 p-0 text-muted-foreground"
          onClick={closePanel}
        >
          <XIcon className="size-4" />
        </Button>
      </div>
      <div className="min-h-0 flex-1">
        <TableSidePanelContent />
      </div>
    </aside>
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
