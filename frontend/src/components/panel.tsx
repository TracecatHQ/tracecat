import { WorkflowForm } from "@/components/forms/workflow"

export function WorkflowPanel() {
  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 flex">
        <div className="flex-1">
          <WorkflowForm />
        </div>
      </div>
    </div>
  )
}
