"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import type { DialogProps } from "@radix-ui/react-dialog"
import {
  Braces,
  ChevronLeft,
  FileKey2,
  KeyRoundIcon,
  PlusCircle,
  Search,
  ShieldCheck,
  Trash2Icon,
} from "lucide-react"
import React, { useMemo, useState } from "react"
import {
  type ArrayPath,
  type FieldPath,
  useFieldArray,
  useForm,
} from "react-hook-form"
import { z } from "zod"
import type { SecretCreate, SecretDefinition } from "@/client"
import { SecretIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { CreateSecretTooltip } from "@/components/secrets/create-secret-tooltip"
import { sshKeyRegex } from "@/components/ssh-keys/ssh-key-utils"
import { SshPrivateKeyField } from "@/components/ssh-keys/ssh-private-key-field"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
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
import { Item, ItemContent, ItemMedia, ItemTitle } from "@/components/ui/item"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"
import { useSecretDefinitions, useWorkspaceSecrets } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

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
          isOptional: z.boolean().optional(),
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
        // Only validate value if it's not an optional field from a template
        // or if user has entered something
        if (!item.isOptional && !item.value?.trim()) {
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

interface CreateCredentialDialogProps extends DialogProps {
  initialView?: "tools" | "custom"
  onOpenChange: (open: boolean) => void
  className?: string
}

export function CreateCredentialDialog({
  initialView = "custom",
  open,
  onOpenChange,
  children,
  className,
}: CreateCredentialDialogProps) {
  const [view, setView] = useState<"tools" | "custom">(initialView)
  const [selectedTool, setSelectedTool] = useState<SecretDefinition | null>(null)
  const workspaceId = useWorkspaceId()
  const { createSecret } = useWorkspaceSecrets(workspaceId)
  const {
    secretDefinitions,
    secretDefinitionsIsLoading,
    secretDefinitionsError,
  } = useSecretDefinitions(workspaceId)
  const [searchQuery, setSearchQuery] = useState("")

  // Reset state when dialog opens/closes
  React.useEffect(() => {
    if (open) {
      setView(initialView)
      setSelectedTool(null)
      setSearchQuery("")
    }
  }, [open, initialView])

  const filteredSecrets = useMemo(() => {
    if (!secretDefinitions) return []

    return secretDefinitions
      .filter((secret) =>
        secret.name.toLowerCase().includes(searchQuery.toLowerCase())
      )
      .sort((a, b) => a.name.localeCompare(b.name))
  }, [secretDefinitions, searchQuery])

  // Form handling
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

  // Update form when tool is selected
  React.useEffect(() => {
    if (selectedTool) {
      const initialKeys = [
        ...(selectedTool.keys || []).map((key) => ({
          key,
          value: "",
          isOptional: false,
        })),
        ...(selectedTool.optional_keys || []).map((key) => ({
          key,
          value: "",
          isOptional: true,
        })),
      ]
      
      methods.reset({
        name: selectedTool.name,
        description: "",
        environment: "default",
        type: "custom",
        keys: initialKeys.length > 0 ? initialKeys : [{ key: "", value: "" }],
        private_key: "",
        tls_certificate: "",
        tls_private_key: "",
        ca_certificate: "",
      })
    } else if (view === "custom") {
        methods.reset({
            name: "",
            description: "",
            environment: "",
            type: "custom",
            keys: [{ key: "", value: "" }],
            private_key: "",
            tls_certificate: "",
            tls_private_key: "",
            ca_certificate: "",
          })
    }
  }, [selectedTool, view, methods])

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

    let secretKeys: { key: string; value: string }[] = []

    switch (type) {
      case "ssh-key":
        secretKeys = [{ key: "PRIVATE_KEY", value: private_key || "" }]
        break
      case "mtls":
        secretKeys = [
          { key: "TLS_CERTIFICATE", value: tls_certificate || "" },
          { key: "TLS_PRIVATE_KEY", value: tls_private_key || "" },
        ]
        break
      case "ca-cert":
        secretKeys = [{ key: "CA_CERTIFICATE", value: ca_certificate || "" }]
        break
      default:
        // Filter out optional keys with empty values
        secretKeys = keys
          .filter((k) => !k.isOptional || (k.value && k.value.trim() !== ""))
          .map(({ key, value }) => ({
            key: key || "",
            value: value || "",
          }))
        break
    }

    if (type === "custom" && secretKeys.length === 0) {
        toast({
            title: "No keys provided",
            description: "Please provide at least one key value.",
            variant: "destructive",
        })
        return
    }

    const secret: SecretCreate = {
      ...rest,
      type,
      keys: secretKeys,
    }
    try {
      await createSecret(secret)
      onOpenChange(false)
      toast({
        title: "Secret created",
        description: `Secret "${values.name}" has been created successfully.`,
      })
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
    <Dialog open={open} onOpenChange={onOpenChange}>
      {children}
      <DialogContent className={`${className} max-h-[85vh] flex flex-col`}>
        <DialogHeader className="flex-shrink-0">
          <div className="flex items-center gap-2">
            {selectedTool && (
                <Button 
                    variant="ghost" 
                    size="icon" 
                    className="-ml-2 h-8 w-8" 
                    onClick={() => setSelectedTool(null)}
                >
                    <ChevronLeft className="h-4 w-4" />
                </Button>
            )}
            <DialogTitle>
                {selectedTool ? `Configure ${selectedTool.name}` : (view === "tools" ? "Select tool" : "Create new secret")}
            </DialogTitle>
          </div>
        </DialogHeader>
        
        {view === "tools" && !selectedTool ? (
             <div className="flex flex-col flex-1 min-h-0 overflow-hidden">
                <div className="mb-4 flex-shrink-0 px-1">
                    <div className="relative">
                    <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
                    <Input
                        placeholder="Search secrets..."
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        className="h-9 border-gray-300 bg-gray-50 pl-10 text-sm focus:border-gray-400 focus:bg-white focus-visible:ring-0 focus-visible:ring-offset-0"
                        autoFocus
                    />
                    </div>
                </div>

                {secretDefinitionsIsLoading ? (
                    <CenteredSpinner />
                ) : secretDefinitionsError ? (
                    <div className="p-4 text-center text-red-500">{secretDefinitionsError.message}</div>
                ) : (
                    <div className="grid grid-cols-2 gap-2 overflow-y-auto p-1">
                    {filteredSecrets.map((secret) => (
                        <Item
                        key={secret.name}
                        variant="outline"
                        className="cursor-pointer hover:bg-muted/50"
                        onClick={() => setSelectedTool(secret)}
                        >
                            <ItemMedia>
                                <SecretIcon
                                secretName={secret.name}
                                className="size-7 rounded"
                                />
                            </ItemMedia>
                            <ItemContent>
                                <ItemTitle className="text-sm">{secret.name}</ItemTitle>
                            </ItemContent>
                        </Item>
                    ))}
                    {filteredSecrets.length === 0 && (
                        <div className="col-span-2 py-8 text-center text-sm text-muted-foreground">
                        No secrets found matching your search.
                        </div>
                    )}
                    </div>
                )}
             </div>
        ) : (
             <>
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
                    
                    {!selectedTool && (
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
                    )}

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
                                        disabled={!!selectedTool && (selectedTool.keys?.includes(field.key || "") || selectedTool.optional_keys?.includes(field.key || ""))}
                                        />
                                    </FormControl>
                                    <FormControl className="flex-1">
                                        <Input
                                        id={`value-${index}`}
                                        className="text-sm"
                                        {...register(
                                            `${inputKey}.${index}.value` as const,
                                            {
                                            required: !field.isOptional,
                                            }
                                        )}
                                        placeholder={field.isOptional ? "Value (optional)" : "Value"}
                                        type="password"
                                        />
                                    </FormControl>

                                    <Button
                                        type="button"
                                        variant="ghost"
                                        onClick={() => remove(index)}
                                        disabled={
                                            (!!selectedTool && (selectedTool.keys?.includes(field.key || "") || selectedTool.optional_keys?.includes(field.key || ""))) || 
                                            (!selectedTool && fields.length === 1)
                                        }
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
                        <Button className="ml-auto space-x-2" type="submit">
                        <KeyRoundIcon className="mr-2 size-4" />
                        Create secret
                        </Button>
                    </DialogFooter>
                </form>
                </Form>
            </>
        )}
      </DialogContent>
    </Dialog>
  )
}
