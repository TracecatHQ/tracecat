"use client"

import React, { PropsWithChildren } from "react"
import { useSession } from "@/providers/session"
import { zodResolver } from "@hookform/resolvers/zod"
import { DialogProps } from "@radix-ui/react-dialog"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useForm } from "react-hook-form"

import { Secret, secretSchema } from "@/types/schemas"
import { createSecret } from "@/lib/secrets"
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
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { toast } from "@/components/ui/use-toast"

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
  const queryClient = useQueryClient()

  const { mutate } = useMutation({
    mutationFn: (secret: Secret) =>
      createSecret(session, secret.name, secret.value),
    onSuccess: (data, variables, context) => {
      toast({
        title: "Added new secret",
        description: "New secret added successfully.",
      })
      queryClient.invalidateQueries({ queryKey: ["secrets"] })
    },
    onError: (error, variables, context) => {
      console.error("Failed to add new credentials", error)
      toast({
        title: "Failed to add new secret",
        description: "An error occurred while adding the new secret.",
      })
    },
  })

  const form = useForm<Secret>({
    resolver: zodResolver(secretSchema),
  })

  const onSubmit = (values: Secret) => {
    console.log("Submitting new secret", values)
    mutate(values)
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
                <Button type="submit" onClick={() => console.log("TEST")}>
                  Add
                </Button>
              </DialogClose>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
export const NewCredentialsDialogTrigger = DialogTrigger
