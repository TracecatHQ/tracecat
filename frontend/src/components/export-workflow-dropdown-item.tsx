import { ToggleableDropdownItem } from "@/components/toggleable-dropdown-item"
import { toast } from "@/components/ui/use-toast"
import { exportWorkflow, handleExportError } from "@/lib/export"

export function ExportMenuItem({
  enabledExport = true,
  format,
  workspaceId,
  workflowId,
  version,
  icon,
  draft = false,
  label,
}: {
  enabledExport?: boolean
  format: "json" | "yaml"
  workspaceId: string
  workflowId: string
  version?: number
  icon?: React.ReactNode
  draft?: boolean
  label?: string
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
            version,
            draft,
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
      {label ?? `Export to ${format.toUpperCase()}`}
    </ToggleableDropdownItem>
  )
}
