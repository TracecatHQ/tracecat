"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { File as FileIcon, Info, Save, Upload, X } from "lucide-react"
import {
  type ChangeEvent,
  type DragEvent,
  useCallback,
  useMemo,
  useRef,
  useState,
} from "react"
import {
  type ControllerRenderProps,
  type UseFormReturn,
  useForm,
  useWatch,
} from "react-hook-form"
import { z } from "zod"
import type { IntegrationUpdate, ProviderRead } from "@/client"
import { MultiTagCommandInput } from "@/components/tags-input"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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
import { useIntegrationProvider } from "@/lib/hooks"
import { isMCPProvider } from "@/lib/providers"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

type EndpointHelp = ProviderRead["authorization_endpoint_help"]

const hasHelpContent = (help: EndpointHelp): boolean => {
  if (help == null) {
    return false
  }
  if (Array.isArray(help)) {
    return help.some((item) => item.trim().length > 0)
  }
  return help.trim().length > 0
}

const renderHelpContent = (help: EndpointHelp) => {
  if (help == null) {
    return null
  }
  const raw = Array.isArray(help) ? help.join("\n") : help
  const sanitized = raw
    .split("\n")
    .map((line) => line.trimEnd())
    .join("\n")
    .trim()

  if (sanitized.length === 0) {
    return null
  }
  const blocks = sanitized.split(/\n{2,}/)
  return blocks.length > 1 ? (
    <div className="space-y-2 text-left whitespace-pre-line">
      {blocks.map((block, index) => (
        <p key={`help-block-${index}`}>{block}</p>
      ))}
    </div>
  ) : (
    <p className="text-left whitespace-pre-line">{sanitized}</p>
  )
}

const createOAuthSchema = (clientSecretMaxLength: number) =>
  z.object({
    client_id: z
      .string()
      .trim()
      .max(512, { message: "Client ID must be 512 characters or less" })
      .optional(),
    client_secret: z
      .string()
      .trim()
      .max(clientSecretMaxLength, {
        message: `Client secret must be ${clientSecretMaxLength} characters or less`,
      })
      .optional(),
    scopes: z.array(z.string().trim().min(1)).optional(),
    authorization_endpoint: z
      .string()
      .trim()
      .url({ message: "Enter a valid HTTPS URL" })
      .refine((value) => value.toLowerCase().startsWith("https://"), {
        message: "Authorization endpoint must use HTTPS",
      }),
    token_endpoint: z
      .string()
      .trim()
      .url({ message: "Enter a valid HTTPS URL" })
      .refine((value) => value.toLowerCase().startsWith("https://"), {
        message: "Token endpoint must use HTTPS",
      }),
  })

type OAuthSchema = z.infer<ReturnType<typeof createOAuthSchema>>

interface ProviderConfigFormProps {
  provider: ProviderRead
  onSuccess?: () => void
  additionalButtons?: React.ReactNode
  formId?: string
  formRef?: React.Ref<HTMLFormElement>
  hideActions?: boolean
  submitLabel?: string
  submitIcon?: React.ReactNode
}

export function ProviderConfigForm({
  provider,
  onSuccess,
  additionalButtons,
  formId,
  formRef,
  hideActions = false,
  submitLabel,
  submitIcon,
}: ProviderConfigFormProps) {
  const workspaceId = useWorkspaceId()
  const isMCP = isMCPProvider(provider)
  const {
    metadata: { id },
    scopes: { default: defaultScopes },
    grant_type: grantType,
    default_authorization_endpoint: providerDefaultAuth,
    default_token_endpoint: providerDefaultToken,
    authorization_endpoint_help: providerAuthHelp,
    token_endpoint_help: providerTokenHelp,
  } = provider

  const isServiceAccountProvider = id === "google"
  const clientSecretMaxLength = isServiceAccountProvider ? 16384 : 512
  const validationSchema = useMemo(
    () => createOAuthSchema(clientSecretMaxLength),
    [clientSecretMaxLength]
  )

  const {
    integration,
    integrationIsLoading,
    updateIntegration,
    updateIntegrationIsPending,
  } = useIntegrationProvider({
    providerId: id,
    workspaceId,
    grantType,
  })

  const defaultValues = useMemo<OAuthSchema>(() => {
    const fallbackScopes = integration?.requested_scopes ?? defaultScopes ?? []
    return {
      client_id: integration?.client_id ?? "",
      client_secret: "",
      scopes: fallbackScopes,
      authorization_endpoint:
        integration?.authorization_endpoint ?? providerDefaultAuth ?? "",
      token_endpoint: integration?.token_endpoint ?? providerDefaultToken ?? "",
    }
  }, [integration, defaultScopes, providerDefaultAuth, providerDefaultToken])

  const form = useForm<OAuthSchema>({
    resolver: zodResolver(validationSchema),
    defaultValues,
  })

  const onSubmit = useCallback(
    async (data: OAuthSchema) => {
      const params: IntegrationUpdate = {
        client_id: data.client_id?.trim() || undefined,
        client_secret: data.client_secret?.trim() || undefined,
        scopes: data.scopes?.length ? data.scopes : undefined,
        authorization_endpoint: data.authorization_endpoint,
        token_endpoint: data.token_endpoint,
        grant_type: grantType,
      }

      await updateIntegration(params)
      onSuccess?.()
    },
    [grantType, onSuccess, updateIntegration]
  )

  const hasAuthHelp = hasHelpContent(providerAuthHelp)
  const hasTokenHelp = hasHelpContent(providerTokenHelp)

  const currentScopes = integration?.requested_scopes ?? []
  const defaultScopesList = useMemo(
    () => (defaultScopes ?? []).filter((scope) => scope.trim().length > 0),
    [defaultScopes]
  )
  const watchedScopes = useWatch({
    control: form.control,
    name: "scopes",
  })
  const scopesValue = watchedScopes ?? []
  const normalizedDefaultScopes = useMemo(
    () => [...defaultScopesList].sort(),
    [defaultScopesList]
  )
  const defaultScopeSuggestions = useMemo(
    () =>
      defaultScopesList.map((scope) => ({
        id: scope,
        label: scope,
        value: scope,
      })),
    [defaultScopesList]
  )
  const normalizedScopesValue = useMemo(
    () => [...scopesValue].sort(),
    [scopesValue]
  )
  const isAtDefaultScopes = useMemo(() => {
    if (normalizedDefaultScopes.length === 0) {
      return normalizedScopesValue.length === 0
    }
    if (normalizedDefaultScopes.length !== normalizedScopesValue.length) {
      return false
    }
    return normalizedDefaultScopes.every(
      (scope, index) => scope === normalizedScopesValue[index]
    )
  }, [normalizedDefaultScopes, normalizedScopesValue])

  const watchedAuthEndpoint = useWatch({
    control: form.control,
    name: "authorization_endpoint",
  })
  const watchedTokenEndpoint = useWatch({
    control: form.control,
    name: "token_endpoint",
  })
  const authEndpointValue = watchedAuthEndpoint ?? ""
  const tokenEndpointValue = watchedTokenEndpoint ?? ""
  const defaultAuthEndpoint = providerDefaultAuth ?? ""
  const defaultTokenEndpoint = providerDefaultToken ?? ""
  const isAtDefaultEndpoints = useMemo(() => {
    return (
      authEndpointValue.trim() === defaultAuthEndpoint.trim() &&
      tokenEndpointValue.trim() === defaultTokenEndpoint.trim()
    )
  }, [
    authEndpointValue,
    tokenEndpointValue,
    defaultAuthEndpoint,
    defaultTokenEndpoint,
  ])

  const handleResetScopes = useCallback(() => {
    const nextValue = defaultScopesList.length > 0 ? [...defaultScopesList] : []
    form.setValue("scopes", nextValue, {
      shouldDirty: !isAtDefaultScopes,
      shouldTouch: true,
    })
    form.clearErrors("scopes")
    void form.trigger("scopes")
  }, [defaultScopesList, form, isAtDefaultScopes])

  const handleResetEndpoints = useCallback(() => {
    form.setValue("authorization_endpoint", defaultAuthEndpoint, {
      shouldDirty: !isAtDefaultEndpoints,
      shouldTouch: true,
    })
    form.setValue("token_endpoint", defaultTokenEndpoint, {
      shouldDirty: !isAtDefaultEndpoints,
      shouldTouch: true,
    })
    form.clearErrors("authorization_endpoint")
    form.clearErrors("token_endpoint")
    void form.trigger("authorization_endpoint")
    void form.trigger("token_endpoint")
  }, [defaultAuthEndpoint, defaultTokenEndpoint, form, isAtDefaultEndpoints])

  const clientIdLabel = isServiceAccountProvider
    ? "Service account email (optional)"
    : "Client ID"
  const clientIdPlaceholder = isServiceAccountProvider
    ? "service-account@project.iam.gserviceaccount.com"
    : "Enter client ID"
  const clientIdDescription = isServiceAccountProvider
    ? "Leave blank to use the service account email from the uploaded key."
    : "The OAuth application's client identifier. Leave blank to remove stored credentials."

  const clientSecretLabel = isServiceAccountProvider
    ? "Service account JSON key"
    : "Client secret"
  const clientSecretPlaceholder = isServiceAccountProvider
    ? "Drag & drop the JSON key (.json) or choose a file"
    : "Enter client secret"
  const clientSecretDescription = isServiceAccountProvider
    ? "Provide the JSON key downloaded from Google Cloud. Leave blank to keep the existing key."
    : "Add or rotate the OAuth client secret. Submit an empty value to keep the existing secret unchanged."
  const hasExistingSecret =
    integration?.status !== undefined
      ? integration.status !== "not_configured"
      : false

  if (integrationIsLoading) {
    return <ProviderConfigFormSkeleton />
  }

  return (
    <div className="flex flex-col gap-6">

      <Form {...form}>
        <form
          id={formId}
          ref={formRef}
          onSubmit={form.handleSubmit(onSubmit)}
          className="flex flex-col gap-6"
        >
          <div className="space-y-4">
            <h3 className="font-medium">Client credentials</h3>
            <div className="flex flex-col gap-4">
              <FormField
                control={form.control}
                name="client_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{clientIdLabel}</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        value={field.value ?? ""}
                        placeholder={clientIdPlaceholder}
                      />
                    </FormControl>
                    <FormMessage />
                    <FormDescription className="text-xs">
                      {clientIdDescription}
                    </FormDescription>
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="client_secret"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{clientSecretLabel}</FormLabel>
                    <FormControl>
                      {isServiceAccountProvider ? (
                        <ServiceAccountJsonUploader
                          field={field}
                          form={form}
                          placeholder={clientSecretPlaceholder}
                          existingConfigured={hasExistingSecret}
                        />
                      ) : (
                        <Input
                          {...field}
                          type="password"
                          value={field.value ?? ""}
                          placeholder={clientSecretPlaceholder}
                        />
                      )}
                    </FormControl>
                    <FormMessage />
                    <FormDescription className="text-xs">
                      {clientSecretDescription}
                    </FormDescription>
                  </FormItem>
                )}
              />
            </div>
          </div>

          <div className="space-y-4">
            <div className="flex items-center justify-between gap-2">
              <h3 className="font-medium">Endpoints</h3>
              {(defaultAuthEndpoint.length > 0 ||
                defaultTokenEndpoint.length > 0 ||
                authEndpointValue.length > 0 ||
                tokenEndpointValue.length > 0) && (
                <Button
                  type="button"
                  variant="link"
                  size="sm"
                  className="px-0"
                  onClick={handleResetEndpoints}
                  disabled={isAtDefaultEndpoints}
                >
                  Reset endpoints
                </Button>
              )}
            </div>
            <div className="flex flex-col gap-4">
              {!isMCP && (hasAuthHelp || hasTokenHelp) && (
                <Alert>
                  <Info className="h-4 w-4" />
                  <AlertDescription className="text-xs">
                    {hasAuthHelp && (
                      <div>
                        <div className="font-medium mb-1">
                          Authorization endpoint:
                        </div>
                        {renderHelpContent(providerAuthHelp)}
                      </div>
                    )}
                    {hasAuthHelp && hasTokenHelp && <div className="mt-3" />}
                    {hasTokenHelp && (
                      <div>
                        <div className="font-medium mb-1">Token endpoint:</div>
                        {renderHelpContent(providerTokenHelp)}
                      </div>
                    )}
                  </AlertDescription>
                </Alert>
              )}
              <FormField
                control={form.control}
                name="authorization_endpoint"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Authorization endpoint</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        value={field.value ?? ""}
                        placeholder={providerDefaultAuth ?? "https://..."}
                      />
                    </FormControl>
                    <FormMessage />
                    <FormDescription className="text-xs">
                      Initiates the OAuth consent flow with the provider. Keep
                      the default unless a custom authorization URL is required.
                    </FormDescription>
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="token_endpoint"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Token endpoint</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        value={field.value ?? ""}
                        placeholder={providerDefaultToken ?? "https://..."}
                      />
                    </FormControl>
                    <FormMessage />
                    <FormDescription className="text-xs">
                      Exchanges authorization codes for tokens at the provider.
                      Keep the default unless a different token URL is required.
                    </FormDescription>
                  </FormItem>
                )}
              />
            </div>
          </div>

          <div className="space-y-4">
            <h3 className="font-medium">Scopes</h3>
            <div className="flex flex-col gap-4">
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-muted-foreground">
                    Default scopes
                  </span>
                </div>
                {defaultScopesList.length ? (
                  <div className="flex flex-wrap gap-1">
                    {defaultScopesList.map((scope) => (
                      <Badge
                        key={scope}
                        variant="secondary"
                        className="text-xs"
                      >
                        {scope}
                      </Badge>
                    ))}
                  </div>
                ) : (
                  <span className="text-xs text-muted-foreground">
                    No default scopes provided.
                  </span>
                )}
              </div>

              <FormField
                control={form.control}
                name="scopes"
                render={({ field }) => (
                  <FormItem>
                    <div className="flex items-center justify-between gap-2">
                      <FormLabel>OAuth scopes</FormLabel>
                      {(defaultScopesList.length > 0 ||
                        scopesValue.length > 0) && (
                        <Button
                          type="button"
                          variant="link"
                          size="sm"
                          className="px-0"
                          onClick={handleResetScopes}
                          disabled={isAtDefaultScopes}
                        >
                          Reset scopes
                        </Button>
                      )}
                    </div>
                    <FormControl>
                      <MultiTagCommandInput
                        value={field.value ?? []}
                        onChange={field.onChange}
                        suggestions={defaultScopeSuggestions}
                        searchKeys={["label", "value"]}
                        allowCustomTags
                        placeholder="Add scopes"
                      />
                    </FormControl>
                    <FormMessage />
                    <FormDescription className="text-xs">
                      Configure the OAuth scopes for this integration.
                    </FormDescription>
                  </FormItem>
                )}
              />
            </div>
          </div>
          {!hideActions && (
            <div className="flex flex-wrap items-center gap-3">
              <Button
                type="submit"
                variant={
                  integration?.status === "configured" ? "outline" : "default"
                }
                className="gap-2"
                disabled={updateIntegrationIsPending}
              >
                {submitIcon ?? <Save className="h-4 w-4" />}
                {submitLabel ?? "Save configuration"}
              </Button>
              {additionalButtons}
            </div>
          )}
        </form>
      </Form>
    </div>
  )
}

interface ServiceAccountJsonUploaderProps {
  field: ControllerRenderProps<OAuthSchema, "client_secret">
  form: UseFormReturn<OAuthSchema>
  placeholder: string
  existingConfigured: boolean
}

function ServiceAccountJsonUploader({
  field,
  form,
  placeholder,
  existingConfigured,
}: ServiceAccountJsonUploaderProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [fileName, setFileName] = useState<string | null>(null)
  const [detectedEmail, setDetectedEmail] = useState<string | null>(null)

  const hasError = Boolean(form.formState.errors.client_secret)

  const resetInput = useCallback(() => {
    if (fileInputRef.current) {
      fileInputRef.current.value = ""
    }
  }, [])

  const clearSelection = useCallback(() => {
    setFileName(null)
    setDetectedEmail(null)
    field.onChange("")
    form.clearErrors("client_secret")
    resetInput()
  }, [field, form, resetInput])

  const handleFile = useCallback(
    (file: File | undefined) => {
      if (!file) {
        return
      }

      if (!file.name.toLowerCase().endsWith(".json")) {
        field.onChange("")
        setFileName(null)
        setDetectedEmail(null)
        form.setError("client_secret", {
          type: "manual",
          message: "Upload a .json file exported from Google Cloud.",
        })
        resetInput()
        return
      }

      const reader = new FileReader()
      reader.onload = () => {
        const text = typeof reader.result === "string" ? reader.result : ""

        try {
          const parsed = JSON.parse(text)
          if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
            throw new Error("Uploaded key must be a JSON object.")
          }
          if (parsed.type !== "service_account") {
            throw new Error('JSON key must include "type": "service_account".')
          }
          if (
            typeof parsed.private_key !== "string" ||
            parsed.private_key.trim().length === 0
          ) {
            throw new Error("JSON key is missing a private_key.")
          }

          const normalized = JSON.stringify(parsed)
          field.onChange(normalized)
          setFileName(file.name)

          const email =
            typeof parsed.client_email === "string"
              ? parsed.client_email.trim()
              : ""
          setDetectedEmail(email || null)

          const currentClientId = form.getValues("client_id")?.trim()
          if ((!currentClientId || currentClientId.length === 0) && email) {
            form.setValue("client_id", email, {
              shouldDirty: true,
              shouldTouch: true,
            })
          }

          form.clearErrors("client_secret")
        } catch (error) {
          field.onChange("")
          setFileName(null)
          setDetectedEmail(null)
          const message =
            error instanceof Error
              ? error.message
              : "Failed to parse service account JSON key."
          form.setError("client_secret", {
            type: "manual",
            message,
          })
        } finally {
          resetInput()
        }
      }
      reader.onerror = () => {
        field.onChange("")
        setFileName(null)
        setDetectedEmail(null)
        form.setError("client_secret", {
          type: "manual",
          message: "Failed to read the uploaded file.",
        })
        resetInput()
      }

      reader.readAsText(file)
    },
    [field, form, resetInput]
  )

  const handleInputChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0]
      handleFile(file)
    },
    [handleFile]
  )

  const handleDrop = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault()
      const file = event.dataTransfer.files?.[0]
      handleFile(file)
      event.dataTransfer.clearData()
    },
    [handleFile]
  )

  const handleDragOver = useCallback((event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = "copy"
  }, [])

  return (
    <>
      <input
        ref={fileInputRef}
        type="file"
        accept=".json,application/json"
        className="hidden"
        onChange={handleInputChange}
      />
      <div
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        className={cn(
          "flex flex-col gap-3 rounded-md border border-dashed p-4 transition-colors",
          hasError
            ? "border-destructive/80 bg-destructive/5"
            : "border-muted-foreground/30 bg-muted/40 hover:border-muted-foreground/50"
        )}
      >
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">
              {fileName ? (
                <span className="inline-flex items-center gap-1 text-sm font-medium text-foreground">
                  <FileIcon className="h-4 w-4" />
                  {fileName}
                </span>
              ) : (
                placeholder
              )}
            </p>
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => fileInputRef.current?.click()}
              >
                <Upload className="mr-2 h-4 w-4" />
                {fileName ? "Replace file" : "Choose JSON"}
              </Button>
              {fileName && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={clearSelection}
                >
                  <X className="mr-1 h-4 w-4" />
                  Remove
                </Button>
              )}
            </div>
          </div>
          {existingConfigured && !fileName && (
            <p className="text-xs text-muted-foreground">
              Existing key remains active until you provide a new file.
            </p>
          )}
          {detectedEmail && (
            <p className="text-xs text-muted-foreground">
              Detected service account:{" "}
              <span className="font-medium">{detectedEmail}</span>
            </p>
          )}
        </div>
      </div>
    </>
  )
}


export function ProviderConfigFormSkeleton() {
  return (
    <div className="animate-pulse rounded-lg border bg-card text-card-foreground shadow-sm">
      <div className="flex flex-col gap-4 p-6">
        <div className="h-7 w-40 rounded-md bg-muted" />
        <div className="h-8 w-full rounded-md bg-muted" />
        <div className="h-8 w-full rounded-md bg-muted" />
        <div className="h-8 w-full rounded-md bg-muted" />
        <div className="mt-4 flex gap-3">
          <div className="h-10 w-32 rounded-md bg-muted" />
          <div className="h-10 w-24 rounded-md bg-muted" />
        </div>
      </div>
    </div>
  )
}
