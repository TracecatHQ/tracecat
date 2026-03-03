"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { Info, Save } from "lucide-react"
import { useCallback, useEffect, useMemo } from "react"
import { useForm, useWatch } from "react-hook-form"
import { z } from "zod"
import type { IntegrationUpdate, ProviderRead } from "@/client"
import { PemFileUploader } from "@/components/pem-file-uploader"
import { ServiceAccountJsonUploader } from "@/components/service-account-json-uploader"
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { useIntegrationProvider } from "@/lib/hooks"
import { isMCPProvider } from "@/lib/providers"
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

const CLIENT_AUTH_METHOD_OPTIONS = [
  {
    value: "auto",
    label: "Auto",
    subtitle:
      "Prefer client assertion when key material exists; otherwise use client secret.",
  },
  {
    value: "client_secret_basic",
    label: "Client secret (basic)",
    subtitle:
      "Sends client_id and client_secret in the Authorization header (Basic auth).",
  },
  {
    value: "client_secret_post",
    label: "Client secret (post)",
    subtitle:
      "Sends client_id and client_secret in the token request body (form fields).",
  },
  {
    value: "private_key_jwt",
    label: "Client assertion (private key JWT)",
    subtitle: "Signs a JWT assertion using your private key and certificate.",
  },
  {
    value: "none",
    label: "No client authentication",
    subtitle: "Public client mode with no client secret or client assertion.",
  },
] as const

interface OAuthSchemaOptions {
  hasExistingClientSecret: boolean
  hasExistingAssertionPrivateKey: boolean
  hasExistingAssertionCertificate: boolean
}

const createOAuthSchema = (
  clientSecretMaxLength: number,
  options: OAuthSchemaOptions
) =>
  z
    .object({
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
      client_auth_method: z.enum([
        "auto",
        "client_secret_basic",
        "client_secret_post",
        "private_key_jwt",
        "none",
      ]),
      client_assertion_private_key: z.string().trim().optional(),
      client_assertion_certificate: z.string().trim().optional(),
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
    .superRefine((data, ctx) => {
      const method = data.client_auth_method
      const hasSecretInput = Boolean(data.client_secret?.trim())
      const hasAssertionKeyInput = Boolean(
        data.client_assertion_private_key?.trim()
      )
      const hasAssertionCertInput = Boolean(
        data.client_assertion_certificate?.trim()
      )
      const hasAssertionKey =
        hasAssertionKeyInput || options.hasExistingAssertionPrivateKey
      const hasAssertionCert =
        hasAssertionCertInput || options.hasExistingAssertionCertificate
      const hasSecret = hasSecretInput || options.hasExistingClientSecret

      if (method === "private_key_jwt") {
        if (!hasAssertionKey) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message: "Private key is required for private key JWT.",
            path: ["client_assertion_private_key"],
          })
        }
        if (!hasAssertionCert) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message: "Certificate is required for private key JWT.",
            path: ["client_assertion_certificate"],
          })
        }
      }

      if (method === "client_secret_basic" || method === "client_secret_post") {
        if (!hasSecret) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message: "Client secret is required for the selected auth method.",
            path: ["client_secret"],
          })
        }
      }

      if (method === "none") {
        if (hasSecretInput) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message:
              "Client secret must be empty when auth method is set to none.",
            path: ["client_secret"],
          })
        }
        if (hasAssertionKeyInput || hasAssertionCertInput) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message:
              "Client assertion fields must be empty when auth method is set to none.",
            path: ["client_assertion_private_key"],
          })
        }
      }

      if (method === "auto" && hasSecret && hasAssertionKey) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message:
            "Auto mode cannot use both client secret and client assertion private key.",
          path: ["client_auth_method"],
        })
      }

      if (hasAssertionCertInput && !hasAssertionKey) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Certificate requires a private key.",
          path: ["client_assertion_private_key"],
        })
      }
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

  const serviceAccountProviders = ["google", "google_sheets", "google_docs"]
  const isServiceAccountProvider = serviceAccountProviders.includes(id)
  const clientSecretMaxLength = isServiceAccountProvider ? 16384 : 512

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
  const hasExistingAssertionPrivateKey =
    integration?.has_client_assertion_private_key ?? false
  const hasExistingAssertionCertificate =
    integration?.has_client_assertion_certificate ?? false
  const hasExistingClientSecret = useMemo(() => {
    if (!integration || integration.status === "not_configured") {
      return false
    }
    if (integration.client_auth_method === "none") {
      return false
    }
    return !(integration.has_client_assertion_private_key ?? false)
  }, [integration])
  const validationSchema = useMemo(
    () =>
      createOAuthSchema(clientSecretMaxLength, {
        hasExistingClientSecret,
        hasExistingAssertionPrivateKey,
        hasExistingAssertionCertificate,
      }),
    [
      clientSecretMaxLength,
      hasExistingClientSecret,
      hasExistingAssertionPrivateKey,
      hasExistingAssertionCertificate,
    ]
  )

  const defaultValues = useMemo<OAuthSchema>(() => {
    const fallbackScopes = integration?.requested_scopes ?? defaultScopes ?? []
    return {
      client_id: integration?.client_id ?? "",
      client_secret: "",
      client_auth_method: integration?.client_auth_method ?? "auto",
      client_assertion_private_key: "",
      client_assertion_certificate: "",
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
        client_auth_method: data.client_auth_method,
        client_assertion_private_key:
          data.client_assertion_private_key?.trim() || undefined,
        client_assertion_certificate:
          data.client_assertion_certificate?.trim() || undefined,
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
  const watchedClientAuthMethod = useWatch({
    control: form.control,
    name: "client_auth_method",
  })
  const watchedTokenEndpoint = useWatch({
    control: form.control,
    name: "token_endpoint",
  })
  const clientAuthMethod = watchedClientAuthMethod ?? "auto"
  const showAssertionFields = clientAuthMethod === "private_key_jwt"
  useEffect(() => {
    if (clientAuthMethod === "private_key_jwt") {
      return
    }

    const hasAssertionKeyInput = Boolean(
      form.getValues("client_assertion_private_key")?.trim()
    )
    const hasAssertionCertInput = Boolean(
      form.getValues("client_assertion_certificate")?.trim()
    )
    if (!hasAssertionKeyInput && !hasAssertionCertInput) {
      return
    }

    form.setValue("client_assertion_private_key", "", {
      shouldDirty: true,
      shouldTouch: true,
    })
    form.setValue("client_assertion_certificate", "", {
      shouldDirty: true,
      shouldTouch: true,
    })
    form.clearErrors([
      "client_assertion_private_key",
      "client_assertion_certificate",
    ])
  }, [clientAuthMethod, form])
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
                    <FormLabel>
                      {clientIdLabel}{" "}
                      <span className="text-xs text-muted-foreground">
                        (optional)
                      </span>
                    </FormLabel>
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
                    <FormLabel>
                      {clientSecretLabel}{" "}
                      <span className="text-xs text-muted-foreground">
                        (optional)
                      </span>
                    </FormLabel>
                    <FormControl>
                      {isServiceAccountProvider ? (
                        <ServiceAccountJsonUploader
                          value={field.value ?? ""}
                          onChange={field.onChange}
                          onError={(message) => {
                            form.setError("client_secret", {
                              type: "manual",
                              message,
                            })
                          }}
                          onClearError={() => {
                            form.clearErrors("client_secret")
                          }}
                          onDetectedEmail={(email) => {
                            const currentClientId = form
                              .getValues("client_id")
                              ?.trim()
                            if (
                              (!currentClientId ||
                                currentClientId.length === 0) &&
                              email
                            ) {
                              form.setValue("client_id", email, {
                                shouldDirty: true,
                                shouldTouch: true,
                              })
                            }
                          }}
                          placeholder={clientSecretPlaceholder}
                          existingConfigured={hasExistingSecret}
                          hasError={Boolean(
                            form.formState.errors.client_secret
                          )}
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

              <FormField
                control={form.control}
                name="client_auth_method"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Client auth method</FormLabel>
                    <FormControl>
                      <Select
                        value={field.value}
                        onValueChange={field.onChange}
                      >
                        <SelectTrigger>
                          <SelectValue placeholder="Select auth method" />
                        </SelectTrigger>
                        <SelectContent>
                          {CLIENT_AUTH_METHOD_OPTIONS.map((option) => (
                            <SelectItem key={option.value} value={option.value}>
                              <div className="flex flex-col gap-0.5 py-0.5">
                                <span>{option.label}</span>
                                <span className="text-xs text-muted-foreground">
                                  {option.subtitle}
                                </span>
                              </div>
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </FormControl>
                    <FormMessage />
                    <FormDescription className="text-xs">
                      Basic auth sends client credentials in the Authorization
                      header; client secret post sends them in the token request
                      body.
                    </FormDescription>
                  </FormItem>
                )}
              />

              {showAssertionFields && (
                <>
                  <FormField
                    control={form.control}
                    name="client_assertion_private_key"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Client assertion private key</FormLabel>
                        <FormControl>
                          <Textarea
                            {...field}
                            value={field.value ?? ""}
                            rows={6}
                            placeholder="-----BEGIN PRIVATE KEY-----"
                          />
                        </FormControl>
                        <PemFileUploader
                          allowedExtensions={[".pem", ".key"]}
                          chooseLabel="Upload key file"
                          onValueLoaded={(value) => {
                            field.onChange(value)
                            form.clearErrors("client_assertion_private_key")
                          }}
                          onError={(message) => {
                            form.setError("client_assertion_private_key", {
                              type: "manual",
                              message,
                            })
                          }}
                          onClearError={() => {
                            form.clearErrors("client_assertion_private_key")
                          }}
                        />
                        <FormMessage />
                        <FormDescription className="text-xs">
                          PEM private key used to sign private key JWT
                          assertions. Leave blank to keep existing key.
                          {hasExistingAssertionPrivateKey
                            ? " Existing key is already configured."
                            : ""}
                        </FormDescription>
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="client_assertion_certificate"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>
                          Client assertion certificate{" "}
                          <span className="text-xs text-muted-foreground">
                            (optional)
                          </span>
                        </FormLabel>
                        <FormControl>
                          <Textarea
                            {...field}
                            value={field.value ?? ""}
                            rows={5}
                            placeholder="-----BEGIN CERTIFICATE-----"
                          />
                        </FormControl>
                        <PemFileUploader
                          allowedExtensions={[".pem", ".crt", ".cer"]}
                          chooseLabel="Upload cert file"
                          onValueLoaded={(value) => {
                            field.onChange(value)
                            form.clearErrors("client_assertion_certificate")
                          }}
                          onError={(message) => {
                            form.setError("client_assertion_certificate", {
                              type: "manual",
                              message,
                            })
                          }}
                          onClearError={() => {
                            form.clearErrors("client_assertion_certificate")
                          }}
                        />
                        <FormMessage />
                        <FormDescription className="text-xs">
                          Optional PEM certificate used for JWT header
                          thumbprint.
                          {hasExistingAssertionCertificate
                            ? " Existing certificate is already configured."
                            : ""}
                        </FormDescription>
                      </FormItem>
                    )}
                  />
                </>
              )}
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
                      <FormLabel>
                        OAuth scopes{" "}
                        <span className="text-xs text-muted-foreground">
                          (optional)
                        </span>
                      </FormLabel>
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
