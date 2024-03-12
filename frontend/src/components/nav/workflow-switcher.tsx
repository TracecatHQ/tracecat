import React from "react"
import { useRouter } from "next/navigation"
import { useWorkflowMetadata } from "@/providers/workflow"
import {
  CaretSortIcon,
  CheckIcon,
  PlusCircledIcon,
} from "@radix-ui/react-icons"
import { Session } from "@supabase/supabase-js"
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
import {
  NewWorkflowDialog,
  NewWorkflowDialogTrigger,
} from "@/components/new-workflow-dialog"

import { Skeleton } from "../ui/skeleton"

type PopoverTriggerProps = React.ComponentPropsWithoutRef<typeof PopoverTrigger>

interface WorkflowSwitcherProps extends PopoverTriggerProps {
  session: Session | null
}

export default function WorkflowSwitcher({
  session,
  className,
}: WorkflowSwitcherProps) {
  const [open, setOpen] = React.useState(false)
  const { workflow } = useWorkflowMetadata()
  const router = useRouter()

  if (!session) {
    console.error("Invalid session, redirecting to login")
    router.push("/login")
    router.refresh()
  }

  const { data: workflows } = useQuery<WorkflowMetadata[], Error>({
    queryKey: ["workflows"],
    queryFn: async () => {
      if (!session) {
        console.error("Invalid session")
        throw new Error("Invalid session")
      }
      const workflows = await fetchAllWorkflows(session)
      return workflows
    },
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
                  workflows.map((workflow) => (
                    <CommandItem
                      key={workflow.id}
                      onSelect={() => {
                        router.push(`/workflows/${workflow.id}`)
                        setOpen(false)
                      }}
                      className="text-xs hover:cursor-pointer"
                    >
                      {/* TODO: Replace with CircleIcon and green / grey / red (error) / yellow (warning) */}
                      <Avatar className="mr-2 h-4 w-4">
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
                          workflow?.id === workflow.id
                            ? "opacity-100"
                            : "opacity-0"
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
