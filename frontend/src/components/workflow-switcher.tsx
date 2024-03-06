"use client"

import React, { useEffect } from "react"
import {
  useSelectedWorkflowMetadata,
  WorkflowMetadata,
} from "@/providers/selected-workflow"
import {
  CaretSortIcon,
  CheckIcon,
  PlusCircledIcon,
} from "@radix-ui/react-icons"
import { useQuery } from "@tanstack/react-query"
import axios from "axios"

import { cn } from "@/lib/utils"
import { Avatar, AvatarImage } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandGroup,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

type PopoverTriggerProps = React.ComponentPropsWithoutRef<typeof PopoverTrigger>

interface WorkflowSwitcherProps extends PopoverTriggerProps {}

export default function WorkflowSwitcher({ className }: WorkflowSwitcherProps) {
  const [open, setOpen] = React.useState(false)
  const [showNewWorkflowDialog, setShowNewWorkflowDialog] =
    React.useState(false)
  const [selectedWorkflow, setSelectedWorkflow] = React.useState<
    WorkflowMetadata | undefined
  >(undefined)

  // Fetch workflows from the database
  const fetchWorkflows = async (): Promise<WorkflowMetadata[]> => {
    try {
      // Attempt to fetch existing workflows
      const response = await axios.get<WorkflowMetadata[]>(
        "http://localhost:8000/workflows"
      )
      let workflows = response.data

      // If no workflows exist, create a new one
      if (workflows.length === 0) {
        const newWorkflowMetadata = JSON.stringify({
          title: "My first workflow",
          description: "Welcome to Tracecat. This is your first workflow!",
        })
        const newWorkflowResponse = await axios.post<WorkflowMetadata>(
          "http://localhost:8000/workflows",
          newWorkflowMetadata,
          {
            headers: {
              "Content-Type": "application/json",
            },
          }
        )
        const newWorkflow = newWorkflowResponse.data
        workflows = [newWorkflow]
      }
      return workflows
    } catch (error) {
      console.error("Error fetching workflows:", error)
      throw error // Rethrow the error to ensure it's caught by useQuery's isError state
    }
  }

  const {
    data: workflows,
    isLoading,
    isError,
  } = useQuery<WorkflowMetadata[], Error>({
    queryKey: ["workflows"],
    queryFn: fetchWorkflows,
  })

  // Automatically select the first workflow as the default selected workflow if not already selected
  const { setSelectedWorkflowMetadata } = useSelectedWorkflowMetadata()

  useEffect(() => {
    if (!selectedWorkflow && workflows && workflows.length > 0) {
      const workflow = workflows[0]
      setSelectedWorkflow(workflow)
      setSelectedWorkflowMetadata(workflow)
    }
  }, [workflows, selectedWorkflow])

  const groups = workflows
    ? [
        {
          label: "workflows", // UI grouping label
          workflows: workflows,
        },
      ]
    : []

  return (
    <Dialog
      open={showNewWorkflowDialog}
      onOpenChange={setShowNewWorkflowDialog}
    >
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            role="combobox"
            aria-expanded={open}
            aria-label="Select a team"
            className={cn("w-96 justify-between", className)}
          >
            {selectedWorkflow?.title ?? ""}
            <CaretSortIcon className="ml-auto h-4 w-4 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-96 p-0">
          <Command>
            <CommandList>
              {groups.map((group) => (
                <CommandGroup key={group.label} heading={group.label}>
                  {group.workflows.map((workflow) => (
                    <CommandItem
                      key={workflow.id}
                      onSelect={() => {
                        setSelectedWorkflow(workflow)
                        setOpen(false)
                      }}
                      className="text-xs"
                    >
                      {/* TODO: Replace with CircleIcon and green / grey / red (error) / yellow (warning) */}
                      <Avatar className="mr-2 h-5 w-5">
                        <AvatarImage
                          src={`https://avatar.vercel.sh/${workflow.id}.png`}
                          alt={workflow.title}
                          className="grayscale"
                        />
                      </Avatar>
                      {workflow.title}
                      <CheckIcon
                        className={cn(
                          "ml-auto h-4 w-4 text-xs",
                          selectedWorkflow &&
                            selectedWorkflow.id === workflow.id
                            ? "opacity-100"
                            : "opacity-0"
                        )}
                      />
                    </CommandItem>
                  ))}
                </CommandGroup>
              ))}
            </CommandList>
            <CommandSeparator />
            <CommandList>
              <CommandGroup>
                <DialogTrigger asChild>
                  <CommandItem
                    className="text-xs"
                    onSelect={() => {
                      setOpen(false)
                      setShowNewWorkflowDialog(true)
                    }}
                  >
                    <PlusCircledIcon className="mr-2 h-5 w-5" />
                    New workflow
                  </CommandItem>
                </DialogTrigger>
              </CommandGroup>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
      {/* Dialog form to create new workflow */}
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New workflow</DialogTitle>
          <DialogDescription>
            Create a new automation workflow.
          </DialogDescription>
        </DialogHeader>
        <div>
          <div className="space-y-4 py-2 pb-4">
            <div className="space-y-2">
              <Label htmlFor="name">Workflow name</Label>
              <Input id="name" />
            </div>
            <div className="space-y-2">
              <Label htmlFor="playbook">Playbook</Label>
              <Select>
                <SelectTrigger>
                  <SelectValue placeholder="Select playbook" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="blank">
                    <span className="font-medium">Blank Canvas</span> -{" "}
                    <span className="text-muted-foreground">
                      Build custom automation workflows.
                    </span>
                  </SelectItem>
                  <SelectItem value="startup-secops">
                    <span className="font-medium">Startup SecOps</span> -{" "}
                    <span className="text-muted-foreground">
                      Automated security operations for startups.
                    </span>
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => setShowNewWorkflowDialog(false)}
          >
            Cancel
          </Button>
          <Button type="submit">Continue</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
