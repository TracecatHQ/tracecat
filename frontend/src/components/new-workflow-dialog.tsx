"use client"

import React, { PropsWithChildren } from "react"
import { useRouter } from "next/navigation"
import { zodResolver } from "@hookform/resolvers/zod"
import { DialogProps } from "@radix-ui/react-dialog"
import { useQueryClient } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { createWorkflow } from "@/lib/flow"
import { Button } from "@/components/ui/button"
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

const newWorkflowFormSchema = z.object({
  title: z.string().min(1, "Please enter a workflow name."),
})

type NewWorkflowFormInputs = z.infer<typeof newWorkflowFormSchema>

interface NewWorkflowDialogProps
  extends PropsWithChildren<
    DialogProps & React.HTMLAttributes<HTMLDivElement>
  > {}

export function NewWorkflowDialog({
  children,
  className,
}: NewWorkflowDialogProps) {
  const [showDialog, setShowDialog] = React.useState(false)
  const queryClient = useQueryClient()
  const router = useRouter()

  const form = useForm<NewWorkflowFormInputs>({
    resolver: zodResolver(newWorkflowFormSchema),
  })

  const onSubmit = async (data: NewWorkflowFormInputs) => {
    try {
      const newWfMetdata = await createWorkflow(data.title)
      form.reset()
      queryClient.invalidateQueries({
        queryKey: ["workflows"],
      })
      router.push(`/workflows/${newWfMetdata.id}`)
    } catch (error) {
      console.error("Failed to create workflow", error)
    }
  }

  return (
    <Dialog open={showDialog} onOpenChange={setShowDialog}>
      {children}
      <DialogContent className={className}>
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
              name="title"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Workflow Name</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="My new workflow"
                      {...field}
                      value={form.watch("title", "")}
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
export const NewWorkflowDialogTrigger = DialogTrigger
