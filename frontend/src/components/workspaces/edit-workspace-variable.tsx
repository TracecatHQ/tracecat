"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import type { DialogProps } from "@radix-ui/react-dialog"
import { PlusCircle, SaveIcon, Trash2Icon } from "lucide-react"
import React, { type PropsWithChildren, useCallback } from "react"
import { useFieldArray, useForm } from "react-hook-form"
import { z } from "zod"
import type { VariableReadMinimal, VariableUpdate } from "@/client"
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
import { useWorkspaceVariables } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

interface EditVariableDialogProps
  extends PropsWithChildren<
    DialogProps & React.HTMLAttributes<HTMLDivElement>
  > {
  selectedVariable: VariableReadMinimal | null
  setSelectedVariable: (selectedVariable: VariableReadMinimal | null) => void
}

const updateVariableFormSchema = z.object({
  name: z.string().optional(),
  description: z.string().max(255).optional(),
  environment: z.string().optional(),
  values: z.array(
    z.object({
      key: z.string(),
      value: z.string(),
    })
  ),
})

type UpdateVariableFormData = z.infer<typeof updateVariableFormSchema>

export function EditVariableDialog({
  selectedVariable,
  setSelectedVariable,
  children,
  className,
  ...props
}: EditVariableDialogProps) {
  const workspaceId = useWorkspaceId()
  const { updateVariableById } = useWorkspaceVariables(workspaceId, {
    listEnabled: false,
  })

  const methods = useForm<UpdateVariableFormData>({
    resolver: zodResolver(updateVariableFormSchema),
    defaultValues: {
      name: "",
      description: "",
      environment: "",
      values: [],
    },
  })
  const { control, register, reset } = methods

  React.useEffect(() => {
    if (selectedVariable) {
      reset({
        name: "",
        description: "",
        environment: "",
        values: Object.entries(selectedVariable.values).map(([key, value]) => ({
          key,
          value:
            typeof value === "object" ? JSON.stringify(value) : String(value),
        })),
      })
    }
  }, [selectedVariable, reset])

  const onSubmit = useCallback(
    async (formValues: UpdateVariableFormData) => {
      if (!selectedVariable) {
        console.error("No variable selected")
        return
      }
      // Convert the array of {key, value} to a dict
      const valuesDict =
        formValues.values && formValues.values.length > 0
          ? Object.fromEntries(
              formValues.values.map(({ key, value }) => [key, value])
            )
          : undefined

      // Remove unset values from the params object
      // We consider empty strings as unset values
      const params: VariableUpdate = {
        name: formValues.name || undefined,
        description: formValues.description || undefined,
        environment: formValues.environment || undefined,
        values: valuesDict,
      }
      try {
        await updateVariableById({
          variableId: selectedVariable.id,
          params,
        })
      } catch (error) {
        console.error(error)
      }
      methods.reset()
      setSelectedVariable(null) // Only unset the selected variable after the form has been submitted
    },
    [selectedVariable, setSelectedVariable, updateVariableById, methods]
  )

  const onValidationFailed = (errors: unknown) => {
    console.error("Form validation failed", errors)
    toast({
      title: "Form validation failed",
      description: "A validation error occurred while editing the variable.",
    })
  }

  const { fields, append, remove } = useFieldArray<UpdateVariableFormData>({
    control,
    name: "values",
  })

  return (
    <Dialog {...props}>
      {children}
      <DialogContent className={className}>
        <DialogHeader>
          <DialogTitle>Edit variable</DialogTitle>
          <DialogDescription className="flex flex-col">
            <span>
              Leave a field blank to keep its existing value. You must update
              all values at once.
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
                        placeholder={selectedVariable?.name || "Name"}
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
                          selectedVariable?.description || "Description"
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
                    <FormLabel className="text-sm">Environment</FormLabel>
                    <FormControl>
                      <Input
                        className="text-sm"
                        placeholder={
                          selectedVariable?.environment || "Environment"
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
                <FormLabel className="text-sm">Values</FormLabel>

                {fields.length > 0 &&
                  fields.map((valuesItem, index) => (
                    <div
                      key={valuesItem.id}
                      className="flex items-center justify-between"
                    >
                      <FormField
                        key={`values.${index}.key`}
                        control={control}
                        name={`values.${index}.key`}
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
                        key={`values.${index}.value`}
                        control={control}
                        name={`values.${index}.value`}
                        render={({ field }) => (
                          <FormItem>
                            <div className="flex flex-col space-y-2">
                              <FormControl>
                                <Input
                                  id={`value-${index}`}
                                  className="text-sm"
                                  placeholder="Value"
                                  type="text"
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
                  Values will be left unchanged.
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

export const EditVariableDialogTrigger = DialogTrigger
