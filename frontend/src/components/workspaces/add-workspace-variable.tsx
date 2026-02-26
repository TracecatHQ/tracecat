"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import type { DialogProps } from "@radix-ui/react-dialog"
import { BracketsIcon, PlusCircle, Trash2Icon } from "lucide-react"
import React, { type PropsWithChildren } from "react"
import {
  type ArrayPath,
  type FieldPath,
  useFieldArray,
  useForm,
} from "react-hook-form"
import { z } from "zod"
import type { VariableCreate } from "@/client"
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
import { CreateVariableTooltip } from "@/components/workspaces/create-variable-tooltip"
import { useWorkspaceVariables } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

interface NewVariableDialogProps
  extends PropsWithChildren<
    DialogProps & React.HTMLAttributes<HTMLDivElement>
  > {}

const createVariableFormSchema = z.object({
  name: z.string().default(""),
  description: z.string().max(255).default(""),
  environment: z
    .string()
    .nullable()
    .transform((val) => val || "default"), // "default" if null or empty
  values: z.array(
    z.object({
      key: z.string(),
      value: z.string(),
    })
  ),
})

type CreateVariableFormData = z.infer<typeof createVariableFormSchema>

export function NewVariableDialog({
  children,
  className,
}: NewVariableDialogProps) {
  const [showDialog, setShowDialog] = React.useState(false)
  const workspaceId = useWorkspaceId()
  const { createVariable } = useWorkspaceVariables(workspaceId, {
    listEnabled: false,
  })

  const methods = useForm<CreateVariableFormData>({
    resolver: zodResolver(createVariableFormSchema),
    defaultValues: {
      name: "",
      description: "",
      environment: "",
      values: [{ key: "", value: "" }],
    },
  })
  const { control, register } = methods

  const onSubmit = async (formValues: CreateVariableFormData) => {
    // Convert the array of {key, value} to a dict
    const valuesDict = Object.fromEntries(
      formValues.values.map(({ key, value }) => [key, value])
    )

    const variableData: VariableCreate = {
      name: formValues.name,
      description: formValues.description,
      environment: formValues.environment,
      values: valuesDict,
    }

    console.log("Submitting new variable", variableData)
    try {
      await createVariable(variableData)
      setShowDialog(false)
    } catch (error) {
      console.log(error)
    }
    methods.reset()
  }
  const onValidationFailed = () => {
    console.error("Form validation failed")
    toast({
      title: "Form validation failed",
      description: "A validation error occurred while adding the new variable.",
    })
  }
  const inputKey = "values"
  const typedKey = inputKey as FieldPath<CreateVariableFormData>
  const { fields, append, remove } = useFieldArray<CreateVariableFormData>({
    control,
    name: inputKey as ArrayPath<CreateVariableFormData>,
  })

  return (
    <Dialog open={showDialog} onOpenChange={setShowDialog}>
      {children}
      <DialogContent className={className}>
        <DialogHeader>
          <DialogTitle>Create new variable</DialogTitle>
        </DialogHeader>
        <CreateVariableTooltip />
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
                        placeholder="Name (lowercase snake case)"
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
                      A description for this variable.
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
                key="environment"
                control={control}
                name="environment"
                render={() => (
                  <FormItem>
                    <FormLabel className="text-sm">Environment</FormLabel>
                    <FormDescription className="text-sm">
                      The workflow&apos;s target execution environment.
                    </FormDescription>
                    <FormControl>
                      <Input
                        className="text-sm"
                        placeholder='Default environment: "default"'
                        {...register("environment")}
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
                    <FormLabel className="text-sm">Values</FormLabel>
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
                                type="text"
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
                    <BracketsIcon className="mr-2 size-4" />
                    Create variable
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

export const NewVariableDialogTrigger = DialogTrigger
