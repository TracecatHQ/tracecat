"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import type { DialogProps } from "@radix-ui/react-dialog"
import {
  AlertTriangleIcon,
  FileTextIcon,
  PlusCircle,
  SaveIcon,
  Trash2Icon,
  UploadCloudIcon,
} from "lucide-react"
import React, { type PropsWithChildren, useCallback } from "react"
import { useFieldArray, useForm } from "react-hook-form"
import { z } from "zod"
import type { SecretUpdate } from "@/client"
import { sshKeyRegex } from "@/components/ssh-keys/ssh-key-utils"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Dialog,
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"
import { useWorkspaceSecrets, type WorkspaceSecretListItem } from "@/lib/hooks"
import { cn } from "@/lib/utils"
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
  okta_auth_method: z.enum(["ssws", "bearer", "private_key"]).default("ssws"),
  okta_base_url: z.string().optional(),
  okta_api_token: z.string().optional(),
  okta_access_token: z.string().optional(),
  okta_service_token: z.string().optional(),
  okta_client_id: z.string().optional(),
  okta_scopes: z.string().optional(),
  okta_kid: z.string().optional(),
  okta_private_key: z.string().optional(),
  okta_dpop_enabled: z.boolean().default(false),
  okta_dpop_key_rotation_interval: z.string().optional(),
})

type EditSecretForm = z.infer<typeof updateSecretSchema>
type OktaAuthMethod = "ssws" | "bearer" | "private_key"

const OKTA_SECRET_NAME = "okta"
const OKTA_MIN_DPOP_KEY_ROTATION_SECONDS = 3600
const OKTA_MAX_DPOP_KEY_ROTATION_SECONDS = 90 * 24 * 3600
const OKTA_CONFIGURED_PLACEHOLDER = "Leave blank to keep configured value"
const OKTA_CONFIGURED_SECRET_PLACEHOLDER = "••••••••••••••••"
const oktaPrivateKeyFileExtensionRegex = /\.(json|pem)$/i
const oktaAuthMethodOptions: {
  value: OktaAuthMethod
  label: string
}[] = [
  {
    value: "ssws",
    label: "API token",
  },
  {
    value: "bearer",
    label: "Bearer token",
  },
  {
    value: "private_key",
    label: "Private key",
  },
]

const fixedSecretTypeKeyNames: Partial<
  Record<WorkspaceSecretListItem["type"], string[]>
> = {
  mtls: ["TLS_CERTIFICATE", "TLS_PRIVATE_KEY"],
  ca_cert: ["CA_CERTIFICATE"],
}

function isOktaCredential(secret: WorkspaceSecretListItem | null) {
  return secret?.type === "custom" && secret.name === OKTA_SECRET_NAME
}

function getInitialOktaAuthMethod(
  secret: WorkspaceSecretListItem
): OktaAuthMethod {
  const keyNames = new Set(secret.keys)
  if (
    keyNames.has("OKTA_PRIVATE_KEY") ||
    keyNames.has("OKTA_CLIENT_ID") ||
    keyNames.has("OKTA_SCOPES")
  ) {
    return "private_key"
  }
  if (keyNames.has("OKTA_ACCESS_TOKEN") || keyNames.has("OKTA_SERVICE_TOKEN")) {
    return "bearer"
  }
  return "ssws"
}

function isValidOktaPrivateKeyJwk(value: string) {
  let parsed: unknown
  try {
    parsed = JSON.parse(value)
  } catch {
    return false
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return false
  }
  const jwk = parsed as Record<string, unknown>
  // Okta private-key auth only supports RSA, and signing requires the private
  // exponent `d` plus the public params `n`/`e`. A public-only or non-JWK
  // object parses as JSON but fails inside the Okta SDK at action-run time.
  const hasField = (field: string) =>
    typeof jwk[field] === "string" && (jwk[field] as string).length > 0
  return jwk.kty === "RSA" && hasField("n") && hasField("e") && hasField("d")
}

function isValidOktaPrivateKey(value: string | undefined) {
  const trimmed = value?.trim()
  if (!trimmed) {
    return false
  }
  if (trimmed.startsWith("{")) {
    return isValidOktaPrivateKeyJwk(trimmed)
  }
  return sshKeyRegex.test(trimmed)
}

function getEditableSecretKeys(secret: WorkspaceSecretListItem) {
  if (secret.type === "ssh_key") {
    return []
  }
  if (secret.is_corrupted && secret.type === "custom") {
    return [{ key: "", value: "" }]
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
  open: controlledOpen,
  onOpenChange,
  ...props
}: EditCredentialsDialogProps) {
  const workspaceId = useWorkspaceId()
  const { updateSecretById } = useWorkspaceSecrets(workspaceId, {
    listEnabled: false,
  })
  const [internalOpen, setInternalOpen] = React.useState(false)
  const isDialogOpen = controlledOpen ?? internalOpen
  const isSshKey = selectedSecret?.type === "ssh_key"
  const hasFixedKeys =
    selectedSecret?.type === "mtls" || selectedSecret?.type === "ca_cert"
  const isOktaCredentialForm = isOktaCredential(selectedSecret)

  const methods = useForm<EditSecretForm>({
    resolver: zodResolver(updateSecretSchema),
    defaultValues: {
      name: "",
      description: "",
      environment: "",
      keys: [],
      okta_auth_method: "ssws",
      okta_base_url: "",
      okta_api_token: "",
      okta_access_token: "",
      okta_service_token: "",
      okta_client_id: "",
      okta_scopes: "",
      okta_kid: "",
      okta_private_key: "",
      okta_dpop_enabled: false,
      okta_dpop_key_rotation_interval: "",
    },
  })
  const { control, register, reset } = methods
  const oktaAuthMethod = methods.watch("okta_auth_method")
  const oktaDpopEnabled = methods.watch("okta_dpop_enabled")

  React.useEffect(() => {
    if (selectedSecret) {
      reset({
        name: "",
        description: "",
        environment: "",
        keys: getEditableSecretKeys(selectedSecret),
        okta_auth_method: getInitialOktaAuthMethod(selectedSecret),
        okta_base_url: "",
        okta_api_token: "",
        okta_access_token: "",
        okta_service_token: "",
        okta_client_id: "",
        okta_scopes: "",
        okta_kid: "",
        okta_private_key: "",
        okta_dpop_enabled: false,
        okta_dpop_key_rotation_interval: "",
      })
    }
  }, [selectedSecret, reset])

  const handleDialogOpenChange = useCallback(
    (nextOpen: boolean) => {
      setInternalOpen(nextOpen)
      onOpenChange?.(nextOpen)
      if (!nextOpen) {
        methods.reset()
        setSelectedSecret(null)
      }
    },
    [methods, onOpenChange, setSelectedSecret]
  )
  const oktaPrivateKeyFileInputRef = React.useRef<HTMLInputElement | null>(null)
  const oktaPrivateKeyReadIdRef = React.useRef(0)
  const [oktaPrivateKeyFileName, setOktaPrivateKeyFileName] = React.useState<
    string | null
  >(null)
  const [isOktaPrivateKeyDragOver, setIsOktaPrivateKeyDragOver] =
    React.useState(false)

  const resetOktaPrivateKeyFileInput = React.useCallback(() => {
    if (oktaPrivateKeyFileInputRef.current) {
      oktaPrivateKeyFileInputRef.current.value = ""
    }
  }, [])

  React.useEffect(() => {
    if (!isDialogOpen) {
      return
    }

    oktaPrivateKeyReadIdRef.current += 1
    setOktaPrivateKeyFileName(null)
    setIsOktaPrivateKeyDragOver(false)
    resetOktaPrivateKeyFileInput()
  }, [isDialogOpen, selectedSecret, resetOktaPrivateKeyFileInput])

  const canPreserveOktaKey = useCallback(
    (key: string) =>
      Boolean(
        selectedSecret &&
          !selectedSecret.is_corrupted &&
          selectedSecret.keys.includes(key)
      ),
    [selectedSecret]
  )

  const addOktaKey = useCallback(
    (
      keys: { key: string; value: string }[],
      key: string,
      value: string | undefined
    ) => {
      const trimmed = value?.trim()
      if (trimmed) {
        keys.push({ key, value: trimmed })
      } else if (canPreserveOktaKey(key)) {
        keys.push({ key, value: "" })
      }
    },
    [canPreserveOktaKey]
  )

  const buildOktaSecretKeys = useCallback(
    (values: EditSecretForm) => {
      const keys: { key: string; value: string }[] = []

      addOktaKey(keys, "OKTA_BASE_URL", values.okta_base_url)

      switch (values.okta_auth_method) {
        case "ssws":
          addOktaKey(keys, "OKTA_API_TOKEN", values.okta_api_token)
          break
        case "bearer":
          addOktaKey(keys, "OKTA_ACCESS_TOKEN", values.okta_access_token)
          addOktaKey(keys, "OKTA_SERVICE_TOKEN", values.okta_service_token)
          break
        case "private_key":
          addOktaKey(keys, "OKTA_CLIENT_ID", values.okta_client_id)
          addOktaKey(keys, "OKTA_SCOPES", values.okta_scopes)
          addOktaKey(keys, "OKTA_KID", values.okta_kid)
          addOktaKey(keys, "OKTA_PRIVATE_KEY", values.okta_private_key)
          if (values.okta_dpop_enabled) {
            keys.push({ key: "OKTA_DPOP_ENABLED", value: "true" })
            addOktaKey(
              keys,
              "OKTA_DPOP_KEY_ROTATION_INTERVAL",
              values.okta_dpop_key_rotation_interval
            )
          } else if (canPreserveOktaKey("OKTA_DPOP_ENABLED")) {
            keys.push({ key: "OKTA_DPOP_ENABLED", value: "" })
            addOktaKey(
              keys,
              "OKTA_DPOP_KEY_ROTATION_INTERVAL",
              values.okta_dpop_key_rotation_interval
            )
          }
          break
      }

      return keys
    },
    [addOktaKey, canPreserveOktaKey]
  )

  const validateOktaEditValues = useCallback(
    (values: EditSecretForm) => {
      if (!selectedSecret || !isOktaCredentialForm) {
        return true
      }

      methods.clearErrors([
        "okta_api_token",
        "okta_access_token",
        "okta_service_token",
        "okta_client_id",
        "okta_scopes",
        "okta_private_key",
        "okta_dpop_key_rotation_interval",
      ])

      const hasValueOrExisting = (key: string, value: string | undefined) =>
        Boolean(value?.trim()) || canPreserveOktaKey(key)

      if (
        values.okta_auth_method === "ssws" &&
        !hasValueOrExisting("OKTA_API_TOKEN", values.okta_api_token)
      ) {
        methods.setError("okta_api_token", {
          type: "manual",
          message: "Okta API token is required.",
        })
        return false
      }

      if (
        values.okta_auth_method === "bearer" &&
        !hasValueOrExisting("OKTA_ACCESS_TOKEN", values.okta_access_token) &&
        !hasValueOrExisting("OKTA_SERVICE_TOKEN", values.okta_service_token)
      ) {
        methods.setError("okta_access_token", {
          type: "manual",
          message: "Access token or service token is required.",
        })
        return false
      }

      if (values.okta_auth_method === "private_key") {
        if (!hasValueOrExisting("OKTA_CLIENT_ID", values.okta_client_id)) {
          methods.setError("okta_client_id", {
            type: "manual",
            message: "Client ID is required.",
          })
          return false
        }
        if (!hasValueOrExisting("OKTA_SCOPES", values.okta_scopes)) {
          methods.setError("okta_scopes", {
            type: "manual",
            message: "Scopes are required.",
          })
          return false
        }
        if (
          values.okta_private_key?.trim() &&
          !isValidOktaPrivateKey(values.okta_private_key)
        ) {
          methods.setError("okta_private_key", {
            type: "manual",
            message:
              "Private key must be a PEM block or an RSA private JWK (kty, n, e, d).",
          })
          return false
        }
        if (
          !values.okta_private_key?.trim() &&
          !canPreserveOktaKey("OKTA_PRIVATE_KEY")
        ) {
          methods.setError("okta_private_key", {
            type: "manual",
            message: "Private key is required.",
          })
          return false
        }

        const rotationInterval = values.okta_dpop_key_rotation_interval?.trim()
        if (values.okta_dpop_enabled && rotationInterval) {
          const seconds = Number(rotationInterval)
          if (!Number.isInteger(seconds)) {
            methods.setError("okta_dpop_key_rotation_interval", {
              type: "manual",
              message: "Rotation interval must be an integer.",
            })
            return false
          }
          if (
            seconds < OKTA_MIN_DPOP_KEY_ROTATION_SECONDS ||
            seconds > OKTA_MAX_DPOP_KEY_ROTATION_SECONDS
          ) {
            methods.setError("okta_dpop_key_rotation_interval", {
              type: "manual",
              message: "Rotation interval must be 3600 to 7776000 seconds.",
            })
            return false
          }
        }
      }

      return true
    },
    [canPreserveOktaKey, isOktaCredentialForm, methods, selectedSecret]
  )

  const handleOktaPrivateKeyFile = React.useCallback(
    (file: File | undefined) => {
      if (!file) {
        return
      }

      const readId = oktaPrivateKeyReadIdRef.current + 1
      oktaPrivateKeyReadIdRef.current = readId

      if (!oktaPrivateKeyFileExtensionRegex.test(file.name)) {
        setOktaPrivateKeyFileName(null)
        methods.setValue("okta_private_key", "", {
          shouldDirty: true,
          shouldTouch: true,
          shouldValidate: false,
        })
        methods.setError("okta_private_key", {
          type: "manual",
          message: "Upload a .pem or .json file.",
        })
        resetOktaPrivateKeyFileInput()
        return
      }

      const reader = new FileReader()
      reader.onload = () => {
        if (readId !== oktaPrivateKeyReadIdRef.current) {
          return
        }

        const privateKey =
          typeof reader.result === "string" ? reader.result : ""

        methods.clearErrors("okta_private_key")
        methods.setValue("okta_private_key", privateKey, {
          shouldDirty: true,
          shouldTouch: true,
          shouldValidate: true,
        })
        setOktaPrivateKeyFileName(file.name)
        resetOktaPrivateKeyFileInput()
      }
      reader.onerror = () => {
        if (readId !== oktaPrivateKeyReadIdRef.current) {
          return
        }

        setOktaPrivateKeyFileName(null)
        methods.setValue("okta_private_key", "", {
          shouldDirty: true,
          shouldTouch: true,
          shouldValidate: false,
        })
        methods.setError("okta_private_key", {
          type: "manual",
          message: "Failed to read the uploaded private key file.",
        })
        resetOktaPrivateKeyFileInput()
      }

      reader.readAsText(file)
    },
    [methods, resetOktaPrivateKeyFileInput]
  )

  const handleOktaPrivateKeyInputChange = React.useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      handleOktaPrivateKeyFile(event.target.files?.[0])
    },
    [handleOktaPrivateKeyFile]
  )

  const handleOktaPrivateKeyDrop = React.useCallback(
    (event: React.DragEvent<HTMLElement>) => {
      event.preventDefault()
      event.stopPropagation()
      setIsOktaPrivateKeyDragOver(false)
      handleOktaPrivateKeyFile(event.dataTransfer.files?.[0])
      event.dataTransfer.clearData()
    },
    [handleOktaPrivateKeyFile]
  )

  const handleOktaPrivateKeyDragOver = React.useCallback(
    (event: React.DragEvent<HTMLElement>) => {
      event.preventDefault()
      event.dataTransfer.dropEffect = "copy"
      setIsOktaPrivateKeyDragOver(true)
    },
    []
  )

  const handleOktaPrivateKeyDragLeave = React.useCallback(
    (event: React.DragEvent<HTMLElement>) => {
      const relatedTarget = event.relatedTarget
      if (
        relatedTarget instanceof Node &&
        event.currentTarget.contains(relatedTarget)
      ) {
        return
      }
      setIsOktaPrivateKeyDragOver(false)
    },
    []
  )

  const handleOktaPrivateKeyFileChoose = React.useCallback(() => {
    oktaPrivateKeyFileInputRef.current?.click()
  }, [])

  const onSubmit = useCallback(
    async (values: EditSecretForm) => {
      if (!selectedSecret) {
        console.error("No secret selected")
        return
      }
      // Remove unset values from the params object
      // We consider empty strings as unset values
      let submittedKeys = values.keys ?? []
      if (isOktaCredentialForm) {
        if (!validateOktaEditValues(values)) {
          return
        }
        submittedKeys = buildOktaSecretKeys(values)
      }
      if (
        selectedSecret.is_corrupted &&
        !isSshKey &&
        submittedKeys.length === 0
      ) {
        methods.setError("keys", {
          type: "manual",
          message: "Add all key names and values to recover this secret.",
        })
        toast({
          title: "Recovery requires all keys",
          description:
            "This secret is corrupted. Re-enter all key names and values before saving.",
          variant: "destructive",
        })
        return
      }
      const params: SecretUpdate = {
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
        handleDialogOpenChange(false)
      } catch (error) {
        console.error(error)
      }
    },
    [
      buildOktaSecretKeys,
      handleDialogOpenChange,
      isOktaCredentialForm,
      isSshKey,
      methods,
      selectedSecret,
      updateSecretById,
      validateOktaEditValues,
    ]
  )

  const onValidationFailed = (errors: unknown) => {
    console.error("Form validation failed", errors)
    toast({
      title: "Form validation failed",
      description: "A validation error occurred while editing the secret.",
    })
  }

  const { fields, append, remove } = useFieldArray<EditSecretForm>({
    control,
    name: "keys",
  })

  return (
    <Dialog
      {...props}
      open={isDialogOpen}
      onOpenChange={handleDialogOpenChange}
    >
      {children}
      <DialogContent className={`${className} max-h-[85vh] flex flex-col`}>
        <DialogHeader className="flex-shrink-0">
          <DialogTitle>Edit secret</DialogTitle>
          <DialogDescription className="flex flex-col">
            {isSshKey ? (
              <span>
                SSH keys are write-once. Delete and recreate the secret to
                rotate the key.
              </span>
            ) : isOktaCredentialForm ? (
              <span>
                Choose the Okta auth method to update. Blank configured fields
                keep their existing values.
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
          <form
            onSubmit={methods.handleSubmit(onSubmit, onValidationFailed)}
            className="flex min-h-0 flex-1 flex-col"
          >
            <div className="flex-1 space-y-4 overflow-y-auto px-1 py-2">
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
              {!isSshKey && isOktaCredentialForm && (
                <div className="space-y-4">
                  <FormField
                    key="okta_auth_method"
                    control={control}
                    name="okta_auth_method"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel className="text-sm">Auth method</FormLabel>
                        <Select
                          onValueChange={(value) => {
                            const authMethod = value as OktaAuthMethod
                            field.onChange(authMethod)
                            methods.clearErrors([
                              "okta_api_token",
                              "okta_access_token",
                              "okta_service_token",
                              "okta_client_id",
                              "okta_scopes",
                              "okta_private_key",
                              "okta_dpop_key_rotation_interval",
                            ])
                          }}
                          value={field.value}
                        >
                          <FormControl>
                            <SelectTrigger className="text-sm">
                              <SelectValue placeholder="Select auth method" />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            {oktaAuthMethodOptions.map((option) => (
                              <SelectItem
                                key={option.value}
                                value={option.value}
                              >
                                {option.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    key="okta_base_url"
                    control={control}
                    name="okta_base_url"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel className="text-sm">Org URL</FormLabel>
                        <FormControl>
                          <Input
                            className="text-sm"
                            placeholder={
                              canPreserveOktaKey("OKTA_BASE_URL")
                                ? OKTA_CONFIGURED_PLACEHOLDER
                                : "https://dev-123456.okta.com"
                            }
                            {...field}
                          />
                        </FormControl>
                        <FormDescription className="text-sm">
                          Optional when actions pass an org URL.
                        </FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  {oktaAuthMethod === "ssws" && (
                    <FormField
                      key="okta_api_token"
                      control={control}
                      name="okta_api_token"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel className="text-sm">API token</FormLabel>
                          <FormControl>
                            <Input
                              className="text-sm"
                              placeholder={
                                canPreserveOktaKey("OKTA_API_TOKEN")
                                  ? OKTA_CONFIGURED_SECRET_PLACEHOLDER
                                  : "Okta SSWS API token"
                              }
                              type="password"
                              {...field}
                            />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  )}

                  {oktaAuthMethod === "bearer" && (
                    <div className="grid gap-4 md:grid-cols-2">
                      <FormField
                        key="okta_access_token"
                        control={control}
                        name="okta_access_token"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel className="text-sm">
                              Access token
                            </FormLabel>
                            <FormControl>
                              <Input
                                className="text-sm"
                                placeholder={
                                  canPreserveOktaKey("OKTA_ACCESS_TOKEN")
                                    ? OKTA_CONFIGURED_SECRET_PLACEHOLDER
                                    : "OAuth access token"
                                }
                                type="password"
                                {...field}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                      <FormField
                        key="okta_service_token"
                        control={control}
                        name="okta_service_token"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel className="text-sm">
                              Service token
                            </FormLabel>
                            <FormControl>
                              <Input
                                className="text-sm"
                                placeholder={
                                  canPreserveOktaKey("OKTA_SERVICE_TOKEN")
                                    ? OKTA_CONFIGURED_SECRET_PLACEHOLDER
                                    : "OAuth service token"
                                }
                                type="password"
                                {...field}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </div>
                  )}

                  {oktaAuthMethod === "private_key" && (
                    <div className="space-y-4">
                      <div className="grid gap-4 md:grid-cols-2">
                        <FormField
                          key="okta_client_id"
                          control={control}
                          name="okta_client_id"
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel className="text-sm">
                                Client ID
                              </FormLabel>
                              <FormControl>
                                <Input
                                  className="text-sm"
                                  placeholder={
                                    canPreserveOktaKey("OKTA_CLIENT_ID")
                                      ? OKTA_CONFIGURED_PLACEHOLDER
                                      : "Okta OAuth client ID"
                                  }
                                  {...field}
                                />
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                        <FormField
                          key="okta_scopes"
                          control={control}
                          name="okta_scopes"
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel className="text-sm">Scopes</FormLabel>
                              <FormControl>
                                <Input
                                  className="text-sm"
                                  placeholder={
                                    canPreserveOktaKey("OKTA_SCOPES")
                                      ? OKTA_CONFIGURED_PLACEHOLDER
                                      : "okta.users.read okta.groups.read"
                                  }
                                  {...field}
                                />
                              </FormControl>
                              <FormDescription className="text-sm">
                                Space or comma separated.
                              </FormDescription>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                      </div>

                      <FormField
                        key="okta_kid"
                        control={control}
                        name="okta_kid"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel className="text-sm">Key ID</FormLabel>
                            <FormControl>
                              <Input
                                className="text-sm"
                                placeholder={
                                  canPreserveOktaKey("OKTA_KID")
                                    ? OKTA_CONFIGURED_PLACEHOLDER
                                    : "Optional JWK key ID"
                                }
                                {...field}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        key="okta_dpop_enabled"
                        control={control}
                        name="okta_dpop_enabled"
                        render={({ field }) => (
                          <FormItem className="space-y-3">
                            <div className="flex items-center justify-between gap-4">
                              <div className="space-y-1">
                                <FormLabel className="text-sm">DPoP</FormLabel>
                                <FormDescription className="text-sm">
                                  {canPreserveOktaKey("OKTA_DPOP_ENABLED")
                                    ? "Toggle on to enable proof-of-possession tokens. Leave off to keep the configured value."
                                    : "Enable proof-of-possession tokens."}
                                </FormDescription>
                              </div>
                              <FormControl>
                                <Switch
                                  checked={field.value}
                                  onCheckedChange={(checked) => {
                                    field.onChange(checked)
                                    if (!checked) {
                                      methods.clearErrors(
                                        "okta_dpop_key_rotation_interval"
                                      )
                                    }
                                  }}
                                />
                              </FormControl>
                            </div>
                            {oktaDpopEnabled && (
                              <FormField
                                key="okta_dpop_key_rotation_interval"
                                control={control}
                                name="okta_dpop_key_rotation_interval"
                                render={({ field: intervalField }) => (
                                  <FormItem>
                                    <FormLabel className="text-sm">
                                      Key rotation interval
                                    </FormLabel>
                                    <FormControl>
                                      <Input
                                        className="text-sm"
                                        inputMode="numeric"
                                        placeholder={
                                          canPreserveOktaKey(
                                            "OKTA_DPOP_KEY_ROTATION_INTERVAL"
                                          )
                                            ? OKTA_CONFIGURED_PLACEHOLDER
                                            : "86400"
                                        }
                                        {...intervalField}
                                      />
                                    </FormControl>
                                    <FormDescription className="text-sm">
                                      Optional seconds; SDK default is 86400.
                                    </FormDescription>
                                    <FormMessage />
                                  </FormItem>
                                )}
                              />
                            )}
                          </FormItem>
                        )}
                      />

                      <FormField
                        key="okta_private_key"
                        control={control}
                        name="okta_private_key"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel className="text-sm">
                              Private key
                            </FormLabel>
                            <input
                              ref={oktaPrivateKeyFileInputRef}
                              type="file"
                              accept=".pem,.json,application/json"
                              className="hidden"
                              onChange={handleOktaPrivateKeyInputChange}
                            />
                            <div
                              onDrop={handleOktaPrivateKeyDrop}
                              onDragEnter={handleOktaPrivateKeyDragOver}
                              onDragOver={handleOktaPrivateKeyDragOver}
                              onDragLeave={handleOktaPrivateKeyDragLeave}
                              className={cn(
                                "rounded-md transition-colors",
                                isOktaPrivateKeyDragOver &&
                                  "ring-1 ring-inset ring-ring"
                              )}
                            >
                              <FormControl>
                                <Textarea
                                  className="h-36 font-mono text-xs"
                                  placeholder={
                                    canPreserveOktaKey("OKTA_PRIVATE_KEY")
                                      ? "Leave blank to keep configured private key"
                                      : "-----BEGIN PRIVATE KEY-----"
                                  }
                                  {...field}
                                  onChange={(event) => {
                                    oktaPrivateKeyReadIdRef.current += 1
                                    setOktaPrivateKeyFileName(null)
                                    methods.clearErrors("okta_private_key")
                                    field.onChange(event)
                                  }}
                                />
                              </FormControl>
                            </div>
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <FormDescription className="text-sm">
                                Paste PEM or JWK JSON, or drop a .pem/.json file
                                into the field.
                              </FormDescription>
                              <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                onClick={handleOktaPrivateKeyFileChoose}
                              >
                                <UploadCloudIcon className="mr-2 size-4" />
                                {oktaPrivateKeyFileName
                                  ? "Replace file"
                                  : "Upload file"}
                              </Button>
                            </div>
                            {oktaPrivateKeyFileName && (
                              <p className="flex items-center gap-1 text-xs text-muted-foreground">
                                <FileTextIcon className="size-3.5" />
                                {oktaPrivateKeyFileName}
                              </p>
                            )}
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </div>
                  )}
                </div>
              )}
              {!isSshKey && !isOktaCredentialForm && (
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
            </div>
            <DialogFooter className="flex-shrink-0 pt-4">
              <Button className="ml-auto space-x-2" type="submit">
                <SaveIcon className="mr-2 size-4" />
                Save
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

export const EditCredentialsDialogTrigger = DialogTrigger
