"use client"

import { Link2 } from "lucide-react"
import { useState } from "react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CaseLinkRowsDialog } from "@/components/cases/case-link-rows-dialog"
import { CaseLinkedTable } from "@/components/cases/case-linked-table"
import {
  CASE_PANEL_ACTION_BOX_CLASS,
  CASE_PANEL_ACTION_ROW_CLASS,
  CASE_PANEL_BOX_CLASS,
} from "@/components/cases/case-task-fields"
import { CaseValueDrawer } from "@/components/cases/case-value-drawer"
import {
  TABLE_PANEL_TITLES,
  TablePanelProvider,
  useTablePanel,
} from "@/components/tables/table-panel-context"
import { TableSidePanelContent } from "@/components/tables/table-side-panel"
import { Skeleton } from "@/components/ui/skeleton"
import { useCaseLinkedTables } from "@/hooks/use-case-rows"
import { useEntitlements } from "@/hooks/use-entitlements"
import { cn } from "@/lib/utils"

/** Props for {@link CaseLinkedRowsSection}. */
export interface CaseLinkedRowsSectionProps {
  caseId: string
  workspaceId: string
}

/**
 * The case's Tables panel: one grid per table with rows linked to the case,
 * each with its own selection for unlinking, and a compact action bar beneath
 * them that opens the link dialog. The action bar doubles as the empty state.
 * Rows page a fixed number at a time behind two arrows in each table's
 * header, so the panel carries no pagination bar; each table also
 * expands into a dialog with room for one.
 *
 * Column definitions ride along on the case-scoped linked-tables summary, so
 * viewing and unlinking need no `table:read`. Linking, unlinking and adding
 * are guarded by `case:update` on the API, so the select and unlink controls
 * only render with that scope. The link controls additionally need
 * `table:read`, because the link dialog reads tables, and adding a new row
 * needs `table:create`. Editing a cell is a write to the table alone, so it
 * follows `table:update`. Without those the grids stay read-only, long text
 * and JSON still open in a read-only view, and the empty state is plain text.
 *
 * The inline grids share one cell panel, shown in the case page's value
 * drawer. An expanded table brings its own, inside its dialog.
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
  // The API takes the insert-and-link request only with both scopes.
  const canAddRow =
    useScopeCheck("case:update", ["table:create"], { all: true }) === true
  // Cell edits go through the table's own row update.
  const canEditCells = useScopeCheck("table:update") === true
  const { hasEntitlement } = useEntitlements()
  const canViewRelatedCases = hasEntitlement("case_addons")
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
    <TablePanelProvider>
      <div className="flex flex-col gap-6">
        {linkedTables.map((linkedTable) => (
          <CaseLinkedTable
            key={linkedTable.table_id}
            caseId={caseId}
            workspaceId={workspaceId}
            tableId={linkedTable.table_id}
            tableName={linkedTable.table_name ?? null}
            rowCount={linkedTable.row_count}
            columns={linkedTable.columns}
            canUpdate={canUpdate}
            canLink={canLink}
            // A deleted source table keeps its links but has no name or
            // columns: there is nothing left to insert into.
            canAddRow={canAddRow && linkedTable.table_name != null}
            canEditCells={canEditCells}
            canViewRelatedCases={canViewRelatedCases}
            onLinkRows={() => openDialog(linkedTable.table_id)}
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
      <CaseRowCellDrawer />
    </TablePanelProvider>
  )
}

/**
 * Puts the cell panel the grids open for long text and JSON in the case
 * page's left drawer, which stays out of the way of the chat on the right.
 * The tables route docks the same panel in a sidebar. Headed by the cell's
 * column, with the mode underneath.
 */
function CaseRowCellDrawer() {
  const { panelOpen, panelContent, closePanel } = useTablePanel()
  const modeTitle = panelContent ? TABLE_PANEL_TITLES[panelContent.mode] : ""

  return (
    <CaseValueDrawer
      open={panelOpen && panelContent !== null}
      onOpenChange={(open) => {
        if (!open) closePanel()
      }}
      title={panelContent?.title ?? modeTitle}
      description={panelContent?.title ? modeTitle : undefined}
    >
      <TableSidePanelContent />
    </CaseValueDrawer>
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
