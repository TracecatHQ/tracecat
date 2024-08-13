"use client"

import React, { PropsWithChildren } from "react"
import { CreateSecretParams, secretsCreateSecret } from "@/client"
import { zodResolver } from "@hookform/resolvers/zod"
import { DialogProps } from "@radix-ui/react-dialog"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { KeyRoundIcon, PlusCircle, Trash2Icon } from "lucide-react"
import { ArrayPath, FieldPath, useFieldArray, useForm } from "react-hook-form"
import SyntaxHighlighter from "react-syntax-highlighter"
import { atomOneDark } from "react-syntax-highlighter/dist/esm/styles/hljs"

import { createSecretSchema } from "@/types/schemas"
import { useWorkspace } from "@/lib/hooks"
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
  const queryClient = useQueryClient()
  const { workspaceId } = useWorkspace()

  const { mutate } = useMutation({
    mutationFn: async (secret: CreateSecretParams) =>
      await secretsCreateSecret({
        workspaceId,
        requestBody: secret,
      }),
    onSuccess: () => {
      toast({
        title: "Added new secret",
        description: "New secret added successfully.",
      })
      queryClient.invalidateQueries({ queryKey: ["secrets"] })
    },
    onError: (error) => {
      console.error("Failed to add new credentials", error)
      toast({
        title: "Failed to add new secret",
        description: "An error occurred while adding the new secret.",
      })
    },
  })

  const methods = useForm<CreateSecretParams>({
    resolver: zodResolver(createSecretSchema),
    defaultValues: {
      name: "",
      type: "custom",
      keys: [{ key: "", value: "" }],
    },
  })
  const { getValues, control, register, watch, trigger } = methods

  const onSubmit = async () => {
    const validated = await trigger()
    if (!validated) {
      console.error("Form validation failed")
      return
    }
    const values = getValues()
    mutate(values)
  }
  const inputKey = "keys"
  const typedKey = inputKey as FieldPath<CreateSecretParams>
  const { fields, append, remove } = useFieldArray<CreateSecretParams>({
    control,
    name: inputKey as ArrayPath<CreateSecretParams>,
  })

  return (
    <Dialog open={showDialog} onOpenChange={setShowDialog}>
      <Form {...methods}>
        <form>
          {children}
          <DialogContent className={className}>
            <DialogHeader>
              <DialogTitle>Create new secret</DialogTitle>
              <div className="flex text-sm leading-relaxed text-muted-foreground">
                <span>
                  Create a secret that can have multiple key-value credential
                  pairs. You can reference these secrets in your workflows
                  through{" "}
                  <InlineHLCode>
                    {"{{ SECRETS.<my_secret>.<key>}}"}
                  </InlineHLCode>
                  {". "}For example, if I have a secret called with key
                  &apos;GH_ACCESS_TOKEN&apos; I can reference this as{" "}
                  <InlineHLCode>
                    {"{{ SECRETS.my_github_secret.GH_ACCESS_TOKEN }}"}
                  </InlineHLCode>
                  {". "}
                </span>
              </div>
            </DialogHeader>
            <div className="space-y-4">
              <FormField
                key="name"
                control={control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-sm">Name</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        className="text-sm"
                        placeholder="Name (snake case)"
                        value={watch("name", "")}
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
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-sm">Description</FormLabel>
                    <FormDescription className="text-sm">
                      A description for this secret.
                    </FormDescription>
                    <FormControl>
                      <Input
                        {...field}
                        className="text-sm"
                        placeholder="Description"
                        value={watch("description") ?? ""}
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
                  <Button
                    role="combobox"
                    className="ml-auto space-x-2"
                    onClick={onSubmit}
                  >
                    <KeyRoundIcon className="mr-2 size-4" />
                    Create Secret
                  </Button>
                </DialogClose>
              </DialogFooter>
            </div>
          </DialogContent>
        </form>
      </Form>
    </Dialog>
  )
}

function InlineHLCode({ children }: { children: string }) {
  return (
    <SyntaxHighlighter
      language="json"
      style={atomOneDark}
      codeTagProps={{ className: "text-foreground/60" }}
      customStyle={{
        display: "inline",
        padding: "0.15rem",
        backgroundColor: "rgba(0,0,0,0.1)",
      }}
      className="rounded-sm"
    >
      {children}
    </SyntaxHighlighter>
  )
}

export const NewCredentialsDialogTrigger = DialogTrigger
