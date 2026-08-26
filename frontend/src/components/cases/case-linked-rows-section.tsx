"use client"

import { Link2, Plus, Unlink2 } from "lucide-react"
import { type ReactNode, useMemo, useState } from "react"
import type { TableRowRead } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CaseLinkRowsDialog } from "@/components/cases/case-link-rows-dialog"
import {
  CASE_PANEL_ACTION_BOX_CLASS,
  CASE_PANEL_ACTION_ROW_CLASS,
  CASE_PANEL_BOX_CLASS,
} from "@/components/cases/case-task-fields"
import { Spinner } from "@/components/loading/spinner"
import { TableRowsGrid } from "@/components/tables/table-rows-grid"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { toast } from "@/components/ui/use-toast"
import {
  useCaseLinkedTables,
  useCaseTableRows,
  useUnlinkCaseRows,
} from "@/hooks/use-case-rows"
import { toGridRow, UNAVAILABLE_ROW_CLASS_RULES } from "@/lib/cases/case-rows"
import { getApiErrorDetail } from "@/lib/errors"
import { useGetTable } from "@/lib/hooks"
import { cn } from "@/lib/utils"

const EMPTY_SELECTION: ReadonlySet<string> = new Set()
const EMPTY_ROWS: readonly TableRowRead[] = []

/** Props for {@link CaseLinkedRowsSection}. */
export interface CaseLinkedRowsSectionProps {
  caseId: string
  workspaceId: string
}

/**
 * The case's Tables panel: one grid per table with every row linked to the
 * case, each with its own selection for unlinking, and a compact action bar
 * beneath them that opens the link dialog. The action bar doubles as the
 * empty state.
 *
 * Every mutation here is guarded by `case:update` on the API, so the link,
 * add, select, and unlink controls only render with that scope. Without it
 * the grids stay, read-only, and the empty state is plain text.
 */
export function CaseLinkedRowsSection({
  caseId,
  workspaceId,
}: CaseLinkedRowsSectionProps) {
  const { linkedTables, linkedTablesIsLoading, linkedTablesError } =
    useCaseLinkedTables({ caseId, workspaceId })
  // Scopes still loading reads as "not permitted", so controls never flash.
  const canUpdate = useScopeCheck("case:update") === true
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
            canUpdate={canUpdate}
            onAddRows={() => openDialog(linkedTable.table_id)}
          />
        ))}
        {canUpdate && (
          <div className={CASE_PANEL_ACTION_BOX_CLASS}>
            <LinkTableRow onClick={() => openDialog()} />
          </div>
        )}
        {!canUpdate && linkedTables.length === 0 && (
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
  /** Whether the viewer holds `case:update`; gates every mutation control. */
  canUpdate: boolean
  onAddRows: () => void
}

function CaseLinkedTableSection({
  caseId,
  workspaceId,
  tableId,
  tableName,
  rowCount,
  canUpdate,
  onAddRows,
}: CaseLinkedTableSectionProps) {
  const [selectedRowIds, setSelectedRowIds] =
    useState<ReadonlySet<string>>(EMPTY_SELECTION)

  const { table, tableIsLoading, tableError } = useGetTable({
    tableId,
    workspaceId,
  })
  const {
    caseTableRows,
    caseTableRowsIsLoading: rowsIsLoading,
    caseTableRowsError: rowsError,
  } = useCaseTableRows({ caseId, tableId, workspaceId })
  const { unlinkCaseRows, unlinkCaseRowsIsPending } = useUnlinkCaseRows({
    caseId,
    workspaceId,
  })

  const rows = useMemo<readonly TableRowRead[]>(
    () =>
      caseTableRows.length > 0 ? caseTableRows.map(toGridRow) : EMPTY_ROWS,
    [caseTableRows]
  )
  const selectedCount = selectedRowIds.size

  async function handleUnlink() {
    const rowIds = [...selectedRowIds]
    if (rowIds.length === 0) return
    try {
      const { unlinkedCount } = await unlinkCaseRows({ tableId, rowIds })
      setSelectedRowIds(EMPTY_SELECTION)
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
          <span className="text-xs text-muted-foreground">
            {rowCount} {rowCount === 1 ? "row" : "rows"}
          </span>
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
          {canUpdate && (
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
        </div>
      </div>
      <div className="overflow-x-auto rounded-md border">
        <div className="min-w-[1200px]">{gridContent}</div>
      </div>
    </div>
  )
}
