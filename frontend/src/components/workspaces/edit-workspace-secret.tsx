"use client"

import React, { PropsWithChildren } from "react"
import { SecretResponse, UpdateSecretParams } from "@/client"
import { zodResolver } from "@hookform/resolvers/zod"
import { DialogProps } from "@radix-ui/react-dialog"
import { KeyRoundIcon, PlusCircle, Trash2Icon } from "lucide-react"
import { ArrayPath, FieldPath, useFieldArray, useForm } from "react-hook-form"

import { createSecretSchema } from "@/types/schemas"
import { useSecrets } from "@/lib/hooks"
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
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { toast } from "@/components/ui/use-toast"

interface EditCredentialsDialogProps
  extends PropsWithChildren<
    DialogProps & React.HTMLAttributes<HTMLDivElement>
  > {
  selectedSecret: SecretResponse | null
  setSelectedSecret: (selectedSecret: SecretResponse | null) => void
}

export function EditCredentialsDialog({
  selectedSecret,
  setSelectedSecret,
  children,
  className,
}: EditCredentialsDialogProps) {
  const [showDialog, setShowDialog] = React.useState(false)
  const { updateSecretById } = useSecrets()
  console.log("EDIT SECRET DIALOG", selectedSecret)

  const methods = useForm<UpdateSecretParams>({
    resolver: zodResolver(createSecretSchema),
    values: {
      name: selectedSecret?.name ?? undefined,
      description: selectedSecret?.description ?? undefined,
      type: "custom",
      keys: selectedSecret?.keys.map((key) => ({
        key,
        value: "",
      })) || [{ key: "", value: "" }],
    },
  })
  const { control, register } = methods

  const onSubmit = async (values: UpdateSecretParams) => {
    if (!selectedSecret) {
      console.error("No secret selected")
      return
    }
    console.log("Submitting edit secret")
    try {
      await updateSecretById({
        secretId: selectedSecret.id,
        params: values,
      })
    } catch (error) {
      console.error(error)
    }
    methods.reset()
  }

  const onValidationFailed = () => {
    console.error("Form validation failed")
    toast({
      title: "Form validation failed",
      description: "A validation error occurred while editing the secret.",
    })
  }

  const inputKey = "keys"
  const typedKey = inputKey as FieldPath<UpdateSecretParams>
  const { fields, append, remove } = useFieldArray<UpdateSecretParams>({
    control,
    name: inputKey as ArrayPath<UpdateSecretParams>,
  })

  return (
    <Dialog
      open={showDialog}
      onOpenChange={(open) => {
        if (!open) {
          setSelectedSecret(null)
        }
        setShowDialog(open)
      }}
    >
      {children}
      <DialogContent className={className}>
        <DialogHeader>
          <DialogTitle>Edit secret</DialogTitle>
          <DialogDescription>
            <b className="inline-block">NOTE</b>: This feature is a work in
            progress.
          </DialogDescription>
        </DialogHeader>
        <Form {...methods}>
          <form onSubmit={methods.handleSubmit(onSubmit, onValidationFailed)}>
            <div className="space-y-4">
              <FormField
                key="name"
                control={control}
                name="name"
                render={() => (
                  <FormItem>
                    <FormLabel className="text-sm">Name</FormLabel>
                    <FormControl>
                      <Input
                        className="text-sm"
                        placeholder="Name (snake case)"
                        {...register("name")}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                key="description"
                control={control}
                name="description"
                render={() => (
                  <FormItem>
                    <FormLabel className="text-sm">Description</FormLabel>
                    <FormDescription className="text-sm">
                      A description for this secret.
                    </FormDescription>
                    <FormControl>
                      <Input
                        className="text-sm"
                        placeholder="Description"
                        {...register("description")}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                key={inputKey}
                control={control}
                name={typedKey}
                render={() => (
                  <FormItem>
                    <FormLabel className="text-sm">Keys</FormLabel>
                    <div className="flex flex-col space-y-2">
                      {fields.map((field, index) => {
                        return (
                          <div
                            key={`${field.id}.${index}`}
                            className="flex w-full items-center gap-2"
                          >
                            <FormControl>
                              <Input
                                id={`key-${index}`}
                                className="text-sm"
                                {...register(
                                  `${inputKey}.${index}.key` as const,
                                  {
                                    required: true,
                                  }
                                )}
                                placeholder="Key"
                              />
                            </FormControl>
                            <FormControl>
                              <Input
                                id={`value-${index}`}
                                className="text-sm"
                                {...register(
                                  `${inputKey}.${index}.value` as const,
                                  {
                                    required: true,
                                  }
                                )}
                                placeholder="••••••••••••••••"
                                type="password"
                              />
                            </FormControl>

                            <Button
                              type="button"
                              variant="ghost"
                              onClick={() => remove(index)}
                              disabled={fields.length === 1}
                            >
                              <Trash2Icon className="size-3.5" />
                            </Button>
                          </div>
                        )
                      })}
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => append({ key: "", value: "" })}
                        className="space-x-2 text-xs"
                      >
                        <PlusCircle className="mr-2 size-4" />
                        Add Item
                      </Button>
                    </div>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <DialogFooter>
                <DialogClose asChild>
                  <Button className="ml-auto space-x-2" type="submit">
                    <KeyRoundIcon className="mr-2 size-4" />
                    Create Secret
                  </Button>
                </DialogClose>
              </DialogFooter>
            </div>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

export const EditCredentialsDialogTrigger = DialogTrigger
