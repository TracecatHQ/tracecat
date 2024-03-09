"use client"

import React, { useEffect } from "react"
import { useParams, useRouter } from "next/navigation"
import { useWorkflowMetadata } from "@/providers/workflow"
import { zodResolver } from "@hookform/resolvers/zod"
import {
  CaretSortIcon,
  CheckIcon,
  PlusCircledIcon,
} from "@radix-ui/react-icons"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { WorkflowMetadata, workflowMetadataSchema } from "@/types/schemas"
import { createWorkflow, fetchWorkflow, fetchWorkflows } from "@/lib/flow"
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
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Skeleton } from "@/components/ui/skeleton"

type PopoverTriggerProps = React.ComponentPropsWithoutRef<typeof PopoverTrigger>

interface WorkflowSwitcherProps extends PopoverTriggerProps {}

const newWorkflowFormSchema = z.object({
  workflowName: z.string().min(1, "Please enter a workflow name."),
})

type WorkflowFormInputs = z.infer<typeof newWorkflowFormSchema>

export default function WorkflowSwitcher({ className }: WorkflowSwitcherProps) {
  const [open, setOpen] = React.useState(false)
  const { setWorkflowMetadata } = useWorkflowMetadata()
  const [showNewWorkflowDialog, setShowNewWorkflowDialog] =
    React.useState(false)
  const params = useParams<{ id: string }>()
  const workflowId = params.id

  const { data: workflows } = useQuery<WorkflowMetadata[], Error>({
    queryKey: ["workflows"],
    queryFn: fetchWorkflows,
  })

  const { data: workflowMetadata } = useQuery<WorkflowMetadata, Error>({
    queryKey: ["workflow", workflowId],
    queryFn: async ({ queryKey }) => {
      const [_, workflowId] = queryKey as [string, string]
      const data = await fetchWorkflow(workflowId)
      const validatedData = workflowMetadataSchema.parse(data)
      setWorkflowMetadata(validatedData)
      return data
    },
  })

  const queryClient = useQueryClient()
  const router = useRouter()

  const form = useForm<WorkflowFormInputs>({
    resolver: zodResolver(newWorkflowFormSchema),
  })

  const onSubmit = async (data: WorkflowFormInputs) => {
    try {
      await createWorkflow(data.workflowName)
      // Assuming you want to do something on successful creation,
      // like showing a notification, refreshing a list of workflows, or resetting the form
      form.reset()
      // Add here any action like closing modal or refreshing the workflow list
      queryClient.invalidateQueries({
        queryKey: ["workflows"],
      })
    } catch (error) {
      console.error("Failed to create workflow", error)
      // Handle error, maybe set an error state or show a toast notification
    }
  }

  if (!workflows) {
    return <Skeleton className="h-9 w-96" />
  }

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
            {workflowMetadata?.title}
            <CaretSortIcon className="ml-auto h-4 w-4 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-96 p-0">
          <Command defaultValue={workflowMetadata?.title}>
            <CommandList>
              <CommandGroup key="workflows" heading="workflows">
                {workflows.map((workflow) => (
                  <CommandItem
                    key={workflow.id}
                    onSelect={() => {
                      setWorkflowMetadata(workflow)
                      router.push(`/workflows/${workflow.id}`)
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
                        workflowMetadata?.id === workflow.id
                          ? "opacity-100"
                          : "opacity-0"
                      )}
                    />
                  </CommandItem>
                ))}
              </CommandGroup>
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
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="workflowName"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Workflow Name</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="My new workflow"
                      {...field}
                      value={form.watch("workflowName", "")}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <DialogFooter>
              <DialogClose asChild>
                <Button type="submit">Create Workflow</Button>
              </DialogClose>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
