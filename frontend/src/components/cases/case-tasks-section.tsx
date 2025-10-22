"use client"

import { Plus } from "lucide-react"
import { useState } from "react"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { useCaseTasks } from "@/lib/hooks"
import { CaseTaskDialog } from "./case-task-dialog"
import { CaseTasksTable } from "./case-tasks-table"

interface CaseTasksSectionProps {
  caseId: string
  workspaceId: string
}

export function CaseTasksSection({
  caseId,
  workspaceId,
}: CaseTasksSectionProps) {
  const { caseTasks, caseTasksIsLoading, caseTasksError } = useCaseTasks({
    caseId,
    workspaceId,
  })
  const [createDialogOpen, setCreateDialogOpen] = useState(false)

  if (caseTasksIsLoading) {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <Skeleton className="h-8 w-32" />
          <Skeleton className="h-8 w-24" />
        </div>
        <Skeleton className="h-[200px] w-full" />
      </div>
    )
  }

  if (caseTasksError) {
    return (
      <div className="flex flex-col items-center justify-center py-8">
        <p className="text-sm text-muted-foreground">Failed to load tasks</p>
        <p className="mt-1 text-xs text-muted-foreground">
          {caseTasksError.message}
        </p>
      </div>
    )
  }

  return (
    <>
      <Accordion type="single" collapsible defaultValue="tasks">
        <AccordionItem value="tasks" className="border-none">
          <div className="flex items-center justify-between gap-2">
            <AccordionTrigger className="py-0 hover:no-underline flex-row-reverse justify-end gap-1">
              <h3 className="text-sm font-medium text-muted-foreground">
                Tasks
              </h3>
            </AccordionTrigger>
            <Button
              size="icon"
              variant="ghost"
              className="size-6"
              onClick={() => setCreateDialogOpen(true)}
            >
              <Plus className="size-4" />
            </Button>
          </div>
          <AccordionContent className="pb-0 pt-3">
            <CaseTasksTable
              tasks={caseTasks || []}
              isLoading={caseTasksIsLoading}
              error={caseTasksError as Error | null}
              caseId={caseId}
              workspaceId={workspaceId}
            />
          </AccordionContent>
        </AccordionItem>
      </Accordion>

      <CaseTaskDialog
        open={createDialogOpen}
        onOpenChange={setCreateDialogOpen}
        task={null}
        caseId={caseId}
        workspaceId={workspaceId}
      />
    </>
  )
}
