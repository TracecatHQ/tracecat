"use client"

import React, { PropsWithChildren, useCallback } from "react"
import { SecretReadMinimal, SecretUpdate } from "@/client"
import { zodResolver } from "@hookform/resolvers/zod"
import { DialogProps } from "@radix-ui/react-dialog"
import { PlusCircle, SaveIcon, Trash2Icon } from "lucide-react"
import { useFieldArray, useForm } from "react-hook-form"
import { z } from "zod"

import { useOrgSecrets } from "@/lib/hooks"
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
import { toast } from "@/components/ui/use-toast"

interface UpdateOrgSecretDialogProps
  extends PropsWithChildren<
    DialogProps & React.HTMLAttributes<HTMLDivElement>
  > {
  selectedSecret: SecretReadMinimal | null
  setSelectedSecret: (selectedSecret: SecretReadMinimal | null) => void
}

const updateOrgSecretSchema = z.object({
  name: z.string().optional(),
  description: z.string().max(255).optional(),
  environment: z.string().optional(),
  keys: z.array(
    z.object({
      key: z.string(),
      value: z.string(),
    })
  ),
})

export function UpdateOrgSecretDialog({
  selectedSecret,
  setSelectedSecret,
  children,
  className,
  ...props
}: UpdateOrgSecretDialogProps) {
  const { updateSecretById } = useOrgSecrets()

  const methods = useForm<SecretUpdate>({
    resolver: zodResolver(updateOrgSecretSchema),
    defaultValues: {
      name: "",
      description: "",
      environment: "",
      keys: [],
    },
  })
  const { control, register, reset } = methods

  React.useEffect(() => {
    if (selectedSecret) {
      reset({
        name: "",
        description: "",
        environment: "",
        keys: selectedSecret.keys.map((keyName) => ({
          key: keyName,
          value: "",
        })),
      })
    }
  }, [selectedSecret, reset])

  const onSubmit = useCallback(
    async (values: SecretUpdate) => {
      if (!selectedSecret) {
        console.error("No secret selected")
        return
      }
      // Remove unset values from the params object
      // We consider empty strings as unset values
      const params = {
        name: values.name || undefined,
        description: values.description || undefined,
        environment: values.environment || undefined,
        keys: values.keys || undefined,
      }
      console.log("Submitting edit secret", params)
      try {
        await updateSecretById({
          secretId: selectedSecret.id,
          params,
        })
      } catch (error) {
        console.error(error)
      }
      methods.reset()
      setSelectedSecret(null) // Only unset the selected secret after the form has been submitted
    },
    [selectedSecret, setSelectedSecret]
  )

  const onValidationFailed = (errors: unknown) => {
    console.error("Form validation failed", errors)
    toast({
      title: "Form validation failed",
      description: "A validation error occurred while editing the secret.",
    })
  }

  const { fields, append, remove } = useFieldArray<SecretUpdate>({
    control,
    name: "keys",
  })

  return (
    <Dialog {...props}>
      {children}
      <DialogContent className={className}>
        <DialogHeader>
          <DialogTitle>Edit secret</DialogTitle>
          <DialogDescription className="flex flex-col">
            <span>
              Leave a field blank to keep its existing value. You must update
              all keys at once.
            </span>
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
                        placeholder={selectedSecret?.name || "Name"}
                        {...register("name")}
                      />
                    </FormControl>
                    <FormMessage />
                    <span className="text-xs text-foreground/50">
                      {!methods.watch("name") && "Name will be left unchanged."}
                    </span>
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
                    <FormControl>
                      <Input
                        className="text-sm"
                        placeholder={
                          selectedSecret?.description || "Description"
                        }
                        {...register("description")}
                      />
                    </FormControl>
                    <FormMessage />
                    <span className="text-xs text-foreground/50">
                      {!methods.watch("description") &&
                        "Description will be left unchanged."}
                    </span>
                  </FormItem>
                )}
              />
              <FormField
                key="environment"
                control={control}
                name="environment"
                render={() => (
                  <FormItem>
                    <FormLabel className="text-sm">Description</FormLabel>
                    <FormControl>
                      <Input
                        className="text-sm"
                        placeholder={
                          selectedSecret?.environment || "Environment"
                        }
                        {...register("environment")}
                      />
                    </FormControl>
                    <FormMessage />
                    <span className="text-xs text-foreground/50">
                      {!methods.watch("environment") &&
                        "Environment will be left unchanged."}
                    </span>
                  </FormItem>
                )}
              />
              <FormItem>
                <FormLabel className="text-sm">Keys</FormLabel>

                {fields.length > 0 &&
                  fields.map((keysItem, index) => (
                    <div
                      key={keysItem.id}
                      className="flex items-center justify-between"
                    >
                      <FormField
                        key={`keys.${index}.key`}
                        control={control}
                        name={`keys.${index}.key`}
                        render={({ field }) => (
                          <FormItem>
                            <FormControl>
                              <Input
                                id={`key-${index}`}
                                className="text-sm"
                                placeholder={"Key"}
                                {...field}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        key={`keys.${index}.value`}
                        control={control}
                        name={`keys.${index}.value`}
                        render={({ field }) => (
                          <FormItem>
                            <div className="flex flex-col space-y-2">
                              <FormControl>
                                <Input
                                  id={`value-${index}`}
                                  className="text-sm"
                                  placeholder="••••••••••••••••"
                                  type="password"
                                  {...field}
                                />
                              </FormControl>
                            </div>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                      <Button
                        type="button"
                        variant="ghost"
                        onClick={() => remove(index)}
                      >
                        <Trash2Icon className="size-3.5" />
                      </Button>
                    </div>
                  ))}
              </FormItem>
              <Button
                type="button"
                variant="outline"
                onClick={() => append({ key: "", value: "" })}
                className="w-full space-x-2 text-xs text-foreground/80"
              >
                <PlusCircle className="mr-2 size-4" />
                Add Item
              </Button>
              {fields.length === 0 && (
                <span className="text-xs text-foreground/50">
                  Secrets will be left unchanged.
                </span>
              )}
              <DialogFooter>
                <DialogClose asChild>
                  <Button className="ml-auto space-x-2" type="submit">
                    <SaveIcon className="mr-2 size-4" />
                    Save
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

export const UpdateOrgSecretDialogTrigger = DialogTrigger
