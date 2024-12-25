import Link from "next/link"
import { WorkflowReadMinimal } from "@/client"

import { cn } from "@/lib/utils"

export function WorkflowItem({
  workspaceId,
  workflow,
}: {
  workspaceId: string
  workflow: WorkflowReadMinimal
}) {
  return (
    <Link
      key={workflow.id}
      href={`/workspaces/${workspaceId}/workflows/${workflow.id}`}
      className={cn(
        "flex min-h-24 min-w-[600px] flex-col items-start justify-start rounded-lg border p-6 text-left text-sm shadow-md transition-all hover:bg-accent",
        "dark:bg-muted dark:text-white dark:hover:bg-muted dark:hover:text-white"
      )}
    >
      <div className="flex w-full flex-col gap-1">
        <div className="flex items-center">
          <div className="flex items-center gap-2">
            <div className="font-semibold capitalize">{workflow.title}</div>
          </div>
          <div className="ml-auto flex items-center space-x-2">
            <span className="text-xs capitalize text-muted-foreground">
              {workflow.status}
            </span>
            <span
              className={cn(
                "flex size-2 rounded-full",
                workflow.status === "online" ? "bg-emerald-600" : "bg-gray-400"
              )}
            />
          </div>
        </div>
        <div className="text-xs font-medium text-muted-foreground">
          {workflow.description ?? ""}
        </div>
      </div>
    </Link>
  )
}
