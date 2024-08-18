import { TriggerCatalog } from "@/components/workbench/catalog/trigger-catalog"
import { UDFCatalog } from "@/components/workbench/catalog/udf-catalog"

export function WorkflowCatalog({ isCollapsed }: { isCollapsed: boolean }) {
  return (
    <div className="no-scrollbar flex flex-col space-y-2 overflow-scroll">
      <TriggerCatalog isCollapsed={isCollapsed} />
      <UDFCatalog isCollapsed={isCollapsed} />
    </div>
  )
}
