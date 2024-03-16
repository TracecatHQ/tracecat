"use client"

import React, { PropsWithChildren } from "react"
import { useSession } from "@/providers/session"
import { zodResolver } from "@hookform/resolvers/zod"
import { DialogProps } from "@radix-ui/react-dialog"
import axios from "axios"
import { useForm } from "react-hook-form"
import { z } from "zod"

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
  name: z.string().min(1, "Please enter a secret name."),
  value: z.string().min(1, "Please enter the secret value."),
})

type NewCredentialsFormInputs = z.infer<typeof newWorkflowFormSchema>

interface NewCredentialsDialogProps
  extends PropsWithChildren<
    DialogProps & React.HTMLAttributes<HTMLDivElement>
  > {}

export function NewCredentialsDialog({
  children,
  className,
}: NewCredentialsDialogProps) {
  const [showDialog, setShowDialog] = React.useState(false)
  const session = useSession()

  const form = useForm<NewCredentialsFormInputs>({
    resolver: zodResolver(newWorkflowFormSchema),
  })

  const onSubmit = async (data: NewCredentialsFormInputs) => {
    try {
      if (!session) {
        throw new Error("Invalid session")
      }
      const response = await axios.post(
        "http://localhost:8001/demo/key",
        JSON.stringify(data),
        {
          headers: {
            "Content-Type": "application/json",
          },
        }
      )
      form.reset()
      console.log("New credentials added", response.data)
    } catch (error) {
      console.error("Failed to add new credentials", error)
    }
  }

  return (
    <Dialog open={showDialog} onOpenChange={setShowDialog}>
      {children}
      <DialogContent className={className}>
        <DialogHeader>
          <DialogTitle>New credential</DialogTitle>
          <DialogDescription>
            Create a new secret. You can refer to this as{" "}
            <code>{"{{ SECRETS.<SECRET_NAME>}}"}</code>
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(onSubmit)}
            className="flex space-x-4"
          >
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormControl>
                    <Input
                      placeholder="Secret Name"
                      {...field}
                      value={form.watch("name", "")}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="value"
              render={({ field }) => (
                <FormItem>
                  <FormControl>
                    <Input
                      placeholder="Secret Value"
                      {...field}
                      value={form.watch("value", "")}
                      type="password"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <DialogFooter>
              <DialogClose asChild>
                <Button type="submit">Create</Button>
              </DialogClose>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
export const NewCredentialsDialogTrigger = DialogTrigger
