"use client"

import React, { PropsWithChildren } from "react"
import { CreateSecretParams } from "@/client"
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

interface NewCredentialsDialogProps
  extends PropsWithChildren<
    DialogProps & React.HTMLAttributes<HTMLDivElement>
  > {}

export function NewCredentialsDialog({
  children,
  className,
}: NewCredentialsDialogProps) {
  const [showDialog, setShowDialog] = React.useState(false)
  const { createSecret } = useSecrets()

  const methods = useForm<CreateSecretParams>({
    resolver: zodResolver(createSecretSchema),
    defaultValues: {
      name: "",
      type: "custom",
      keys: [{ key: "", value: "" }],
    },
  })
  const { control, register } = methods

  const onSubmit = async (values: CreateSecretParams) => {
    console.log("Submitting new secret")
    try {
      await createSecret(values)
    } catch (error) {
      console.error(error)
    }
    methods.reset()
  }
  const onValidationFailed = () => {
    console.error("Form validation failed")
    toast({
      title: "Form validation failed",
      description: "A validation error occurred while adding the new secret.",
    })
  }
  const inputKey = "keys"
  const typedKey = inputKey as FieldPath<CreateSecretParams>
  const { fields, append, remove } = useFieldArray<CreateSecretParams>({
    control,
    name: inputKey as ArrayPath<CreateSecretParams>,
  })

  return (
    <Dialog open={showDialog} onOpenChange={setShowDialog}>
      {children}
      <DialogContent className={className}>
        <DialogHeader>
          <DialogTitle>Create new secret</DialogTitle>
          <div className="flex text-sm leading-relaxed text-muted-foreground">
            <span>
              Create a secret that can have multiple key-value credential pairs.
              You can reference these secrets in your workflows through{" "}
              <p className="inline-block rounded-sm bg-amber-100 p-[0.75px] font-mono">
                {"${{ SECRETS.<my_secret>.<key>}}"}
              </p>
              {". "}For example, if I have a secret called with key{" "}
              <p className="inline-block font-mono">GH_ACCESS_TOKEN</p>, I can
              reference this as{" "}
              <p className="inline-block rounded-sm bg-amber-100 p-[0.75px] font-mono">
                {"${{ SECRETS.my_github_secret.GH_ACCESS_TOKEN }}"}
              </p>
              {". "}
            </span>
          </div>
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
                                placeholder="Value"
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

export const NewCredentialsDialogTrigger = DialogTrigger
