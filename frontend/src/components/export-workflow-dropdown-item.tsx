import { exportWorkflow, handleExportError } from "@/lib/export"
import { toast } from "@/components/ui/use-toast"
import { ToggleableDropdownItem } from "@/components/toggleable-dropdown-item"

export function ExportMenuItem({
  enabledExport = true,
  format,
  workspaceId,
  workflowId,
  icon,
}: {
  enabledExport?: boolean
  format: "json" | "yaml"
  workspaceId: string
  workflowId: string
  icon?: React.ReactNode
}) {
  return (
    <ToggleableDropdownItem
      enabled={enabledExport}
      onSelect={async () => {
        if (!enabledExport) return

        try {
          await exportWorkflow({
            workspaceId,
            workflowId,
            format,
          })
        } catch (error) {
          console.error(
            `Failed to download workflow definition as ${format}:`,
            error
          )
          toast(handleExportError(error as Error))
        }
      }}
    >
      {icon}
      Export to {format.toUpperCase()}
    </ToggleableDropdownItem>
  )
}
