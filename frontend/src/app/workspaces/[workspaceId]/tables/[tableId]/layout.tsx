"use client"

import { XIcon } from "lucide-react"
import type React from "react"
import { ControlsHeader } from "@/components/nav/controls-header"
import { AppSidebar } from "@/components/sidebar/app-sidebar"
import {
  TablePanelProvider,
  useTablePanel,
} from "@/components/tables/table-panel-context"
import { TableSelectionProvider } from "@/components/tables/table-selection-context"
import { TableSidePanelContent } from "@/components/tables/table-side-panel"
import { Button } from "@/components/ui/button"
import { ResizableSidebar } from "@/components/ui/resizable-sidebar"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"

const PANEL_TITLES: Record<string, string> = {
  "view-json": "View JSON",
  "edit-text": "Edit text",
  "edit-json": "Edit JSON",
}

export default function TableDetailLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <TableSelectionProvider>
      <TablePanelProvider>
        <SidebarProvider>
          <AppSidebar />
          <SidebarInset className="min-w-0 flex-1 mr-px">
            <div className="flex h-full flex-col">
              <ControlsHeader />
              <div className="flex-1 overflow-y-auto">{children}</div>
            </div>
          </SidebarInset>
          <TableSidePanel />
        </SidebarProvider>
      </TablePanelProvider>
    </TableSelectionProvider>
  )
}

function TableSidePanel() {
  const { panelOpen, panelContent, closePanel } = useTablePanel()

  if (!panelOpen || !panelContent) return null

  return (
    <ResizableSidebar initial={450} min={350} max={700}>
      <div className="flex h-full flex-col">
        <div className="flex shrink-0 items-center justify-between px-4 py-2">
          <span className="truncate text-sm font-medium">
            {PANEL_TITLES[panelContent.mode] ?? "Panel"}
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="size-6 p-0"
            onClick={closePanel}
          >
            <XIcon className="size-4" />
          </Button>
        </div>
        <div className="min-h-0 flex-1">
          <TableSidePanelContent />
        </div>
      </div>
    </ResizableSidebar>
  )
}
