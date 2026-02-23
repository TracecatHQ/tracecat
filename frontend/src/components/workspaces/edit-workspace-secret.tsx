"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import type { DialogProps } from "@radix-ui/react-dialog"
import {
  AlertTriangleIcon,
  PlusCircle,
  SaveIcon,
  Trash2Icon,
} from "lucide-react"
import React, { type PropsWithChildren, useCallback } from "react"
import { useFieldArray, useForm } from "react-hook-form"
import { z } from "zod"
import type { SecretUpdate } from "@/client"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
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
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"
import { useWorkspaceSecrets, type WorkspaceSecretListItem } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

interface EditCredentialsDialogProps
  extends PropsWithChildren<
    DialogProps & React.HTMLAttributes<HTMLDivElement>
  > {
  selectedSecret: WorkspaceSecretListItem | null
  setSelectedSecret: (selectedSecret: WorkspaceSecretListItem | null) => void
}

const updateSecretSchema = z.object({
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

const fixedSecretTypeKeyNames: Partial<
  Record<WorkspaceSecretListItem["type"], string[]>
> = {
  mtls: ["TLS_CERTIFICATE", "TLS_PRIVATE_KEY"],
  "ca-cert": ["CA_CERTIFICATE"],
}

function getEditableSecretKeys(secret: WorkspaceSecretListItem) {
  if (secret.type === "ssh-key") {
    return []
  }
  const keyNames =
    secret.keys.length > 0
      ? secret.keys
      : (fixedSecretTypeKeyNames[secret.type] ?? [])
  return keyNames.map((keyName) => ({ key: keyName, value: "" }))
}

export function EditCredentialsDialog({
  selectedSecret,
  setSelectedSecret,
  children,
  className,
  ...props
}: EditCredentialsDialogProps) {
  const workspaceId = useWorkspaceId()
  const { updateSecretById } = useWorkspaceSecrets(workspaceId)
  const isSshKey = selectedSecret?.type === "ssh-key"
  const hasFixedKeys =
    selectedSecret?.type === "mtls" || selectedSecret?.type === "ca-cert"

  const methods = useForm<SecretUpdate>({
    resolver: zodResolver(updateSecretSchema),
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
        keys: getEditableSecretKeys(selectedSecret),
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
      const submittedKeys = values.keys ?? []
      const params = {
        name: values.name || undefined,
        description: values.description || undefined,
        environment: values.environment || undefined,
        keys:
          isSshKey || submittedKeys.length === 0 ? undefined : submittedKeys,
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
    [isSshKey, methods, selectedSecret, setSelectedSecret, updateSecretById]
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
            {isSshKey ? (
              <span>
                SSH keys are write-once. Delete and recreate the secret to
                rotate the key.
              </span>
            ) : hasFixedKeys ? (
              <span>
                Key names are fixed for this secret type. Leave a field blank to
                keep its existing value.
              </span>
            ) : (
              <span>
                Leave a field blank to keep its existing value. You must update
                all keys at once.
              </span>
            )}
          </DialogDescription>
        </DialogHeader>
        <Form {...methods}>
          <form onSubmit={methods.handleSubmit(onSubmit, onValidationFailed)}>
            <div className="space-y-4">
              {selectedSecret?.is_corrupted && (
                <Alert>
                  <AlertTriangleIcon className="size-4 !text-amber-600" />
                  <AlertTitle>Unable to decrypt current key data</AlertTitle>
                  <AlertDescription>
                    {isSshKey
                      ? "This SSH key secret cannot be edited. Delete and recreate it with a new key."
                      : "Stored key names and values could not be decrypted. Re-enter all key names and values before saving."}
                  </AlertDescription>
                </Alert>
              )}
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
                    <FormLabel className="text-sm">Environment</FormLabel>
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
              {!isSshKey && (
                <>
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
                                    readOnly={hasFixedKeys}
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
                                    {hasFixedKeys ? (
                                      <Textarea
                                        id={`value-${index}`}
                                        className="h-32 text-sm"
                                        placeholder={
                                          keysItem.key?.includes("PRIVATE_KEY")
                                            ? "-----BEGIN PRIVATE KEY-----"
                                            : "-----BEGIN CERTIFICATE-----"
                                        }
                                        {...field}
                                      />
                                    ) : (
                                      <Input
                                        id={`value-${index}`}
                                        className="text-sm"
                                        placeholder="••••••••••••••••"
                                        type="password"
                                        {...field}
                                      />
                                    )}
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
                            disabled={hasFixedKeys}
                          >
                            <Trash2Icon className="size-3.5" />
                          </Button>
                        </div>
                      ))}
                  </FormItem>
                  {!hasFixedKeys && (
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => append({ key: "", value: "" })}
                      className="w-full space-x-2 text-xs text-foreground/80"
                    >
                      <PlusCircle className="mr-2 size-4" />
                      Add Item
                    </Button>
                  )}
                  {fields.length === 0 && !hasFixedKeys && (
                    <span className="text-xs text-foreground/50">
                      Secrets will be left unchanged.
                    </span>
                  )}
                </>
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

export const EditCredentialsDialogTrigger = DialogTrigger
