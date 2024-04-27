import React from "react"
import { usePathname, useRouter } from "next/navigation"
import { useWorkflowMetadata } from "@/providers/workflow"
import {
  CaretSortIcon,
  CheckIcon,
  PlusCircledIcon,
} from "@radix-ui/react-icons"
import { useQuery } from "@tanstack/react-query"

import { WorkflowMetadata } from "@/types/schemas"
import { fetchAllWorkflows } from "@/lib/flow"
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
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Skeleton } from "@/components/ui/skeleton"
import {
  NewWorkflowDialog,
  NewWorkflowDialogTrigger,
} from "@/components/new-workflow-dialog"

type PopoverTriggerProps = React.ComponentPropsWithoutRef<typeof PopoverTrigger>

interface WorkflowSwitcherProps extends PopoverTriggerProps {}

export default function WorkflowSwitcher({ className }: WorkflowSwitcherProps) {
  const [open, setOpen] = React.useState(false)
  const { workflow } = useWorkflowMetadata()
  const router = useRouter()
  const pathname = usePathname()

  const { data: workflows } = useQuery<WorkflowMetadata[], Error>({
    queryKey: ["workflows"],
    queryFn: fetchAllWorkflows,
  })

  return (
    <NewWorkflowDialog>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            role="combobox"
            aria-expanded={open}
            aria-label="Select a team"
            className={cn("w-96 justify-between", className)}
          >
            {workflow?.title}
            <CaretSortIcon className="ml-auto h-4 w-4 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-96 p-0">
          <Command defaultValue={workflow?.title}>
            <CommandList>
              <CommandGroup key="workflows" heading="workflows">
                {!workflows ? (
                  <Skeleton />
                ) : (
                  workflows.map((wf) => (
                    <CommandItem
                      key={wf.id}
                      onSelect={() => {
                        // If we're on cases page, stay at /workflows/{workflowId}/cases
                        const nextPath = pathname.endsWith("/cases")
                          ? "/cases"
                          : pathname.endsWith("/console")
                            ? "/console"
                            : ""
                        const fullPath = `/workflows/${wf.id}` + nextPath
                        router.push(fullPath)
                        setOpen(false)
                      }}
                      className="text-xs hover:cursor-pointer"
                    >
                      {/* TODO: Replace with CircleIcon and green / grey / red (error) / yellow (warning) */}
                      <Avatar className="mr-2 h-4 w-4">
                        <AvatarImage
                          src={`https://avatar.vercel.sh/${wf.id}.png`}
                          alt={wf.title}
                          className="grayscale"
                        />
                      </Avatar>
                      {wf.title}
                      <CheckIcon
                        className={cn(
                          "ml-auto h-4 w-4 text-xs",
                          workflow?.id === wf.id ? "opacity-100" : "opacity-0"
                        )}
                      />
                    </CommandItem>
                  ))
                )}
              </CommandGroup>
            </CommandList>
            <CommandSeparator />
            <CommandList>
              <CommandGroup>
                <CommandItem
                  className="text-xs"
                  onSelect={() => setOpen(false)}
                >
                  <NewWorkflowDialogTrigger asChild>
                    <Button
                      variant="ghost"
                      className="ml-0 h-5 w-full justify-start p-0 text-left text-xs font-normal"
                    >
                      <PlusCircledIcon className="mr-2 h-5 w-5" />
                      New workflow
                    </Button>
                  </NewWorkflowDialogTrigger>
                </CommandItem>
              </CommandGroup>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
    </NewWorkflowDialog>
  )
}
