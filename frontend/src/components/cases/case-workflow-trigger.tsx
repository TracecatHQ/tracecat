"use client"

import fuzzysort from "fuzzysort"
import { ArrowUpRight, ChevronsUpDown, PlayIcon } from "lucide-react"
import Link from "next/link"
import { useCallback, useMemo, useState } from "react"
import type { CaseRead } from "@/client"
import { JsonViewWithControls } from "@/components/json-viewer"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { Label } from "@/components/ui/label"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { TooltipProvider } from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { useLocalStorage } from "@/hooks/use-local-storage"
import {
  useCreateManualWorkflowExecution,
  useWorkflowManager,
} from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

interface CaseWorkflowTriggerProps {
  caseData: CaseRead
}

/**
 * Renders a workflow trigger section for a case.
 * Allows selecting a workflow and triggering it with the case data as input.
 * @param caseData The data of the current case.
 * @returns JSX.Element
 */
export function CaseWorkflowTrigger({ caseData }: CaseWorkflowTriggerProps) {
  const workspaceId = useWorkspaceId()
  // Get the manual execution hook for the selected workflow (if any)
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(
    null
  )
  const [searchTerm, setSearchTerm] = useState("")
  const [isComboboxOpen, setIsComboboxOpen] = useState(false)
  // Use the useLocalStorage hook
  const [groupCaseFields, setGroupCaseFields] = useLocalStorage(
    "groupCaseFields",
    false
  )

  const { createExecution, createExecutionIsPending } =
    useCreateManualWorkflowExecution(selectedWorkflowId || "")
  const triggerInputs = useMemo(() => {
    const fields = Object.fromEntries(
      caseData.fields
        .filter((field) => !field.reserved)
        .map((field) => [field.id, field.value])
    )
    if (groupCaseFields) {
      return {
        case_id: caseData.id,
        case_fields: fields,
      }
    }
    return {
      case_id: caseData.id,
      ...fields,
    }
  }, [caseData, groupCaseFields])
  const [isConfirmOpen, setIsConfirmOpen] = useState(false)

  const selectedWorkflowUrl = `/workspaces/${workspaceId}/workflows/${selectedWorkflowId}`
  // Fetch workflows
  const { workflows, workflowsLoading, workflowsError } = useWorkflowManager()

  const searchableWorkflows = useMemo(
    () =>
      (workflows ?? []).map((workflow) => ({
        workflow,
        title: workflow.title,
        alias: workflow.alias ?? "",
      })),
    [workflows]
  )

  const filteredWorkflows = useMemo(() => {
    if (!searchableWorkflows.length) {
      return []
    }

    if (!searchTerm.trim()) {
      return searchableWorkflows
    }

    const results = fuzzysort.go(searchTerm, searchableWorkflows, {
      all: true,
      keys: ["title", "alias"],
    })

    return results.map((result) => result.obj)
  }, [searchableWorkflows, searchTerm])

  const handleTrigger = useCallback(async () => {
    if (!selectedWorkflowId) return
    await createExecution({
      workflow_id: selectedWorkflowId,
      inputs: triggerInputs,
    })
    toast({
      title: "Workflow run started",
      description: (
        <Link
          href={selectedWorkflowUrl}
          target="_blank"
          rel="noopener noreferrer"
        >
          <div className="flex items-center space-x-1">
            <ArrowUpRight className="size-3" />
            <span>View workflow run</span>
          </div>
        </Link>
      ),
    })
  }, [createExecution, selectedWorkflowId, triggerInputs])

  // Loading state
  if (workflowsLoading) {
    return <Skeleton className="h-8 w-full" />
  }

  // Error state
  if (workflowsError) {
    return (
      <div className="text-xs text-destructive">
        Error loading workflows: {workflowsError.message}
      </div>
    )
  }

  const selectedWorkflow = workflows?.find((wf) => wf.id === selectedWorkflowId)
  return (
    <div className="space-y-3">
      <Popover
        open={isComboboxOpen}
        onOpenChange={(open) => {
          setIsComboboxOpen(open)
          if (!open) {
            setSearchTerm("")
          }
        }}
      >
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            role="combobox"
            aria-expanded={isComboboxOpen}
            className="h-8 w-full justify-between border-muted text-xs"
          >
            <span className="flex min-w-0 items-center gap-2 truncate">
              {selectedWorkflow ? (
                <>
                  <span className="truncate">{selectedWorkflow.title}</span>
                  {selectedWorkflow.alias && (
                    <Badge
                      variant="secondary"
                      className="px-1 py-0 text-[10px] font-normal"
                    >
                      {selectedWorkflow.alias}
                    </Badge>
                  )}
                </>
              ) : (
                <span className="text-muted-foreground">
                  Select a workflow...
                </span>
              )}
            </span>
            <ChevronsUpDown className="ml-2 size-3 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent
          className="w-[--radix-popover-trigger-width] min-w-64 p-0"
          align="start"
        >
          <Command shouldFilter={false}>
            <CommandInput
              placeholder="Search workflows..."
              value={searchTerm}
              onValueChange={setSearchTerm}
            />
            <CommandList>
              {workflowsLoading ? (
                <CommandEmpty>Loading workflows...</CommandEmpty>
              ) : workflowsError ? (
                <CommandEmpty>Failed to load workflows</CommandEmpty>
              ) : filteredWorkflows.length === 0 ? (
                <CommandEmpty>No workflows found</CommandEmpty>
              ) : (
                <CommandGroup>
                  {filteredWorkflows.map(({ workflow }) => (
                    <CommandItem
                      key={workflow.id}
                      value={workflow.id}
                      onSelect={() => {
                        setSelectedWorkflowId(workflow.id)
                        setIsComboboxOpen(false)
                        setSearchTerm("")
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
              )}
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>

      <AlertDialog open={isConfirmOpen} onOpenChange={setIsConfirmOpen}>
        <AlertDialogTrigger asChild>
          <Button
            variant="outline"
            size="sm"
            disabled={!selectedWorkflowId || createExecutionIsPending}
            className="w-full h-8 text-xs"
          >
            <PlayIcon className="mr-1.5 h-3 w-3" />
            Trigger
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent className="max-w-md">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-sm">
              Confirm workflow trigger
            </AlertDialogTitle>
            <AlertDialogDescription className="text-xs">
              Are you sure you want to trigger &quot;{selectedWorkflow?.title}
              &quot; with the following inputs?
            </AlertDialogDescription>
          </AlertDialogHeader>

          <div className="mt-4">
            <TooltipProvider>
              <JsonViewWithControls
                src={triggerInputs}
                showControls={false}
                defaultTab="nested"
                defaultExpanded
              />
            </TooltipProvider>

            <div className="mt-4 flex items-center space-x-2">
              <Switch
                id="group-fields"
                checked={groupCaseFields}
                onCheckedChange={setGroupCaseFields}
                className="h-4 w-8"
              />
              <Label htmlFor="group-fields" className="text-xs">
                Group case fields
              </Label>
            </div>
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel className="text-xs">Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleTrigger} className="text-xs">
              <PlayIcon className="mr-1.5 h-3 w-3" />
              Trigger
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {selectedWorkflowId && (
        <Link
          href={selectedWorkflowUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowUpRight className="h-3 w-3" />
          <span>View workflow</span>
        </Link>
      )}
    </div>
  )
}
