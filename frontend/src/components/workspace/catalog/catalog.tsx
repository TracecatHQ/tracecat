import { TriggerCatalog } from "@/components/workspace/catalog/trigger-catalog"
import { UDFCatalog } from "@/components/workspace/catalog/udf-catalog"

export function WorkflowCatalog({ isCollapsed }: { isCollapsed: boolean }) {
  return (
    <div className="flex flex-col space-y-2">
      <TriggerCatalog isCollapsed={isCollapsed} />
      <UDFCatalog isCollapsed={isCollapsed} />
    </div>
  )
}
