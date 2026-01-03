"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import type { DialogProps } from "@radix-ui/react-dialog"
import {
  Braces,
  FileKey2,
  KeyRoundIcon,
  PlusCircle,
  ShieldCheck,
  Trash2Icon,
} from "lucide-react"
import React, { type PropsWithChildren } from "react"
import {
  type ArrayPath,
  type FieldPath,
  useFieldArray,
  useForm,
} from "react-hook-form"
import { z } from "zod"
import type { SecretCreate } from "@/client"
import { CreateSecretTooltip } from "@/components/secrets/create-secret-tooltip"
import { sshKeyRegex } from "@/components/ssh-keys/ssh-key-utils"
import { SshPrivateKeyField } from "@/components/ssh-keys/ssh-private-key-field"
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"
import { useWorkspaceSecrets } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

interface NewCredentialsDialogProps
  extends PropsWithChildren<
    DialogProps & React.HTMLAttributes<HTMLDivElement>
  > {}

const pemCertificateRegex =
  /-----BEGIN CERTIFICATE-----(?:\r?\n)[A-Za-z0-9+/=\s]+(?:\r?\n)-----END CERTIFICATE-----/

const validatePemField = (
  value: string | undefined,
  ctx: z.RefinementCtx,
  path: string[],
  requiredMessage: string,
  invalidMessage: string,
  regex: RegExp
) => {
  if (!value?.trim()) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      path,
      message: requiredMessage,
    })
    return
  }
  if (!regex.test(value)) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      path,
      message: invalidMessage,
    })
  }
}

const createSecretSchema = z
  .object({
    name: z.string().default(""),
    description: z.string().max(255).default(""),
    environment: z
      .string()
      .nullable()
      .transform((val) => val || "default"), // "default" if null or empty
    type: z.enum(["custom", "ssh-key", "mtls", "ca-cert"]).default("custom"),
    keys: z
      .array(
        z.object({
          key: z.string().optional(),
          value: z.string().optional(),
        })
      )
      .default([]),
    private_key: z.string().optional(),
    tls_certificate: z.string().optional(),
    tls_private_key: z.string().optional(),
    ca_certificate: z.string().optional(),
  })
  .superRefine((values, ctx) => {
    if (values.type === "custom") {
      if (!values.keys.length) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["keys"],
          message: "At least one key is required.",
        })
      }
      values.keys.forEach((item, index) => {
        if (!item.key?.trim()) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ["keys", index, "key"],
            message: "Key is required.",
          })
        }
        if (!item.value?.trim()) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ["keys", index, "value"],
            message: "Value is required.",
          })
        }
      })
    }

    if (values.type === "ssh-key") {
      validatePemField(
        values.private_key,
        ctx,
        ["private_key"],
        "SSH private key is required.",
        "Invalid SSH private key format. Must include PEM header and footer.",
        sshKeyRegex
      )
    }

    if (values.type === "mtls") {
      validatePemField(
        values.tls_certificate,
        ctx,
        ["tls_certificate"],
        "TLS certificate is required.",
        "Invalid TLS certificate format. Must include PEM header and footer.",
        pemCertificateRegex
      )
      validatePemField(
        values.tls_private_key,
        ctx,
        ["tls_private_key"],
        "TLS private key is required.",
        "Invalid TLS private key format. Must include PEM header and footer.",
        sshKeyRegex
      )
    }

    if (values.type === "ca-cert") {
      validatePemField(
        values.ca_certificate,
        ctx,
        ["ca_certificate"],
        "CA certificate is required.",
        "Invalid CA certificate format. Must include PEM header and footer.",
        pemCertificateRegex
      )
    }
  })

type CreateSecretForm = z.infer<typeof createSecretSchema>

export function NewCredentialsDialog({
  children,
  className,
}: NewCredentialsDialogProps) {
  const [showDialog, setShowDialog] = React.useState(false)
  const workspaceId = useWorkspaceId()
  const { createSecret } = useWorkspaceSecrets(workspaceId)

  const methods = useForm<CreateSecretForm>({
    resolver: zodResolver(createSecretSchema),
    defaultValues: {
      name: "",
      description: "",
      environment: "",
      type: "custom",
      keys: [{ key: "", value: "" }],
      private_key: "",
      tls_certificate: "",
      tls_private_key: "",
      ca_certificate: "",
    },
  })
  const { control, register } = methods
  const secretType = methods.watch("type")

  const renderTextareaField = (
    name: FieldPath<CreateSecretForm>,
    label: string,
    placeholder: string
  ) => (
    <FormField
      key={name}
      control={control}
      name={name}
      render={() => (
        <FormItem>
          <FormLabel className="text-sm">{label}</FormLabel>
          <FormControl>
            <Textarea
              className="h-36 text-sm"
              placeholder={placeholder}
              {...register(name)}
            />
          </FormControl>
          <FormMessage />
        </FormItem>
      )}
    />
  )

  const onSubmit = async (values: CreateSecretForm) => {
    const {
      private_key,
      tls_certificate,
      tls_private_key,
      ca_certificate,
      type,
      keys,
      ...rest
    } = values
    const secretKeys = (() => {
      switch (type) {
        case "ssh-key":
          return [{ key: "PRIVATE_KEY", value: private_key || "" }]
        case "mtls":
          return [
            { key: "TLS_CERTIFICATE", value: tls_certificate || "" },
            { key: "TLS_PRIVATE_KEY", value: tls_private_key || "" },
          ]
        case "ca-cert":
          return [{ key: "CA_CERTIFICATE", value: ca_certificate || "" }]
        default:
          return keys.map(({ key, value }) => ({
            key: key || "",
            value: value || "",
          }))
      }
    })()
    const secret: SecretCreate = {
      ...rest,
      type,
      keys: secretKeys,
    }
    try {
      await createSecret(secret)
    } catch (error) {
      console.log(error)
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
  const typedKey = inputKey as FieldPath<CreateSecretForm>
  const { fields, append, remove } = useFieldArray<CreateSecretForm>({
    control,
    name: inputKey as ArrayPath<CreateSecretForm>,
  })

  return (
    <Dialog open={showDialog} onOpenChange={setShowDialog}>
      {children}
      <DialogContent className={`${className} max-h-[85vh] flex flex-col`}>
        <DialogHeader className="flex-shrink-0">
          <DialogTitle>Create new secret</DialogTitle>
        </DialogHeader>
        <CreateSecretTooltip />
        <Form {...methods}>
          <form
            onSubmit={methods.handleSubmit(onSubmit, onValidationFailed)}
            className="flex flex-col flex-1 min-h-0"
          >
            <div className="space-y-4 overflow-y-auto flex-1 py-2 px-1">
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
                key="type"
                control={control}
                name="type"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-sm">Type</FormLabel>
                    <FormDescription className="text-sm">
                      Choose how this secret is stored.
                    </FormDescription>
                    <Select onValueChange={field.onChange} value={field.value}>
                      <FormControl>
                        <SelectTrigger className="text-sm">
                          <SelectValue placeholder="Select a type" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        <SelectItem value="custom">
                          <span className="flex items-center gap-2">
                            <Braces className="size-4" />
                            Key-value pair
                          </span>
                        </SelectItem>
                        <SelectItem value="ssh-key">
                          <span className="flex items-center gap-2">
                            <FileKey2 className="size-4" />
                            SSH private key
                          </span>
                        </SelectItem>
                        <SelectItem value="mtls">
                          <span className="flex items-center gap-2">
                            <KeyRoundIcon className="size-4" />
                            mTLS certificate + key
                          </span>
                        </SelectItem>
                        <SelectItem value="ca-cert">
                          <span className="flex items-center gap-2">
                            <ShieldCheck className="size-4" />
                            CA certificate
                          </span>
                        </SelectItem>
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />
              {secretType === "custom" && (
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
                              <FormControl className="flex-1">
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
                              <FormControl className="flex-1">
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
              )}
              {secretType === "ssh-key" && (
                <SshPrivateKeyField
                  control={control}
                  register={register}
                  name="private_key"
                />
              )}
              {secretType === "mtls" && (
                <>
                  {renderTextareaField(
                    "tls_certificate",
                    "TLS certificate",
                    "-----BEGIN CERTIFICATE-----"
                  )}
                  {renderTextareaField(
                    "tls_private_key",
                    "TLS private key",
                    "-----BEGIN PRIVATE KEY-----"
                  )}
                </>
              )}
              {secretType === "ca-cert" &&
                renderTextareaField(
                  "ca_certificate",
                  "CA certificate",
                  "-----BEGIN CERTIFICATE-----"
                )}
            </div>
            <DialogFooter className="flex-shrink-0 pt-4">
              <DialogClose asChild>
                <Button className="ml-auto space-x-2" type="submit">
                  <KeyRoundIcon className="mr-2 size-4" />
                  Create secret
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
