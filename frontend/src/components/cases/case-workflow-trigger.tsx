"use client"

import { useCallback, useEffect, useState } from "react"
import type { CaseRead } from "@/client"
import { CASE_WORKFLOW_TRIGGER_EVENT } from "@/components/cases/case-panel-common"
import { WorkflowTriggerDialog } from "@/components/cases/workflow-trigger-dialog"
import { Badge } from "@/components/ui/badge"
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { useWorkflowManager } from "@/lib/hooks"

interface CaseWorkflowTriggerProps {
  caseData: CaseRead
}

export function CaseWorkflowTrigger({ caseData }: CaseWorkflowTriggerProps) {
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(
    null
  )
  const [selectedWorkflowTitle, setSelectedWorkflowTitle] = useState<
    string | null
  >(null)
  const [isCommandOpen, setIsCommandOpen] = useState(false)
  const [isDialogOpen, setIsDialogOpen] = useState(false)

  const openCommandPalette = useCallback(() => {
    setIsCommandOpen(true)
  }, [])

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        if (event.repeat) {
          return
        }
        event.preventDefault()
        setIsCommandOpen((open) => !open)
      }
    }

    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [])

  useEffect(() => {
    const handleOpen = () => {
      openCommandPalette()
    }

    window.addEventListener(CASE_WORKFLOW_TRIGGER_EVENT, handleOpen)
    return () => {
      window.removeEventListener(CASE_WORKFLOW_TRIGGER_EVENT, handleOpen)
    }
  }, [openCommandPalette])

  const { workflows, workflowsLoading, workflowsError } = useWorkflowManager()

  if (workflowsLoading) {
    return null
  }

  if (workflowsError) {
    console.error("Failed to load workflows", workflowsError)
    return null
  }

  const availableWorkflows = workflows ?? []

  return (
    <>
      <CommandDialog
        open={isCommandOpen}
        onOpenChange={(open) => setIsCommandOpen(open)}
      >
        <CommandInput placeholder="Search workflows..." />
        <CommandList>
          <CommandEmpty>No workflows found</CommandEmpty>
          {availableWorkflows.length > 0 ? (
            <CommandGroup heading="Workflows">
              {availableWorkflows.map((workflow) => (
                <CommandItem
                  key={workflow.id}
                  value={`${workflow.title} ${workflow.alias ?? ""}`.trim()}
                  onSelect={() => {
                    setSelectedWorkflowId(workflow.id)
                    setSelectedWorkflowTitle(workflow.title)
                    setIsCommandOpen(false)
                    setIsDialogOpen(true)
                  }}
                  className="flex flex-col items-start py-2"
                >
                  <div className="flex w-full items-center gap-2">
                    <span className="truncate font-medium">
                      {workflow.title}
                    </span>
                    {workflow.alias && (
                      <Badge
                        variant="secondary"
                        className="px-1 py-0 text-[10px] font-normal"
                      >
                        {workflow.alias}
                      </Badge>
                    )}
                  </div>
                </CommandItem>
              ))}
            </CommandGroup>
          ) : null}
        </CommandList>
      </CommandDialog>

      <WorkflowTriggerDialog
        caseData={caseData}
        workflowId={selectedWorkflowId}
        workflowTitle={selectedWorkflowTitle}
        open={isDialogOpen && Boolean(selectedWorkflowId)}
        onOpenChange={(open) => {
          setIsDialogOpen(open)
          if (!open) {
            setSelectedWorkflowId(null)
            setSelectedWorkflowTitle(null)
          }
        }}
      />
    </>
  )
}
