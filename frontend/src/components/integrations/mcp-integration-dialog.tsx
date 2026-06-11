"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  Check,
  ExternalLink,
  Globe2,
  Loader2,
  Plus,
  Server,
  Trash2,
} from "lucide-react"
import React, { useState } from "react"
import { useFieldArray, useForm } from "react-hook-form"
import {
  integrationsGetIntegration,
  mcpIntegrationsConnectPlatformMcpCatalog,
  providersCreateCustomProvider,
} from "@/client/services.gen"
import type {
  MCPCatalogConnectResponse,
  MCPConnectionSpec,
  MCPHttpIntegrationCreate,
  MCPIntegrationRead,
  MCPIntegrationUpdate,
  MCPStdioIntegrationCreate,
  PlatformMCPCatalogRead,
} from "@/client/types.gen"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CodeEditor } from "@/components/editor/codemirror/code-editor"
import { ProviderIcon } from "@/components/icons"
import {
  ALLOWED_COMMANDS,
  AUTH_TYPES,
  catalogEntryToFormValues,
  isAllowedCommand,
  MCP_INTEGRATION_FORM_DEFAULTS,
  type MCPIntegrationFormValues,
  mcpIntegrationFormSchema,
  missingRequiredOAuthClientCredentials,
  normalizeOAuthClientKey,
  SERVER_TYPES,
} from "@/components/integrations/mcp-integration-schema"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button, type ButtonProps } from "@/components/ui/button"
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
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"
import { getMcpOAuthConnectErrorDetail } from "@/lib/errors"
import {
  useConnectMcpIntegration,
  useCreateMcpIntegration,
  useDeleteMcpIntegration,
  useGetMcpIntegration,
  useIntegrations,
  useUpdateMcpIntegration,
} from "@/lib/hooks"
import { isMcpProvider } from "@/lib/integrations"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

function transportLabel(
  spec: PlatformMCPCatalogRead["connection_spec"] | null | undefined
) {
  return spec?.server_type?.toUpperCase() ?? null
}

function optionIcon(
  spec: PlatformMCPCatalogRead["connection_spec"] | null | undefined
) {
  return spec?.server_type === "stdio" ? Server : Globe2
}

function catalogOptionIdForIntegration(
  entry: PlatformMCPCatalogRead | null | undefined,
  integration: MCPIntegrationRead
) {
  const options = entry?.connection_options ?? []
  const match = options.find((option) => {
    const spec = option.connection_spec
    if (!spec || spec.server_type !== integration.server_type) {
      return false
    }
    if (spec.server_type === "http") {
      return (
        !integration.server_uri ||
        !("server_uri" in spec) ||
        spec.server_uri === integration.server_uri
      )
    }
    return true
  })
  return match?.id ?? ""
}

function catalogSpecForOption(
  entry: PlatformMCPCatalogRead | null | undefined,
  optionId: string | null | undefined
): MCPConnectionSpec | null {
  if (!entry) {
    return null
  }
  const option = (entry.connection_options ?? []).find(
    (candidate) => candidate.id === optionId
  )
  return option?.connection_spec ?? entry.connection_spec ?? null
}

function hasOAuthClientConfig(spec: MCPConnectionSpec | null | undefined) {
  if (spec?.server_type !== "http" || spec.auth_type !== "OAUTH2") {
    return false
  }
  return (
    (spec.config_fields ?? []).some(
      (field) => field.target === "oauth_client"
    ) ||
    (spec.credentials ?? []).some(
      (credential) => credential.target === "oauth_client"
    )
  )
}

function isClientSecretKey(key: string) {
  const normalized = normalizeOAuthClientKey(key)
  return (
    normalized === "client_secret" ||
    normalized === "oauth_client_secret" ||
    normalized.endsWith("_client_secret")
  )
}

function isClientIdKey(key: string) {
  const normalized = normalizeOAuthClientKey(key)
  return (
    normalized === "client_id" ||
    normalized === "oauth_client_id" ||
    normalized.endsWith("_client_id")
  )
}

function readOAuthClientCredentials(value: string) {
  const parsed = JSON.parse(value) as Record<string, string>
  const entries = Object.entries(parsed)
  const clientIdEntry =
    entries.find(([key]) => isClientIdKey(key)) ??
    entries.find(([key]) => !isClientSecretKey(key))
  const clientSecretEntry = entries.find(([key]) => isClientSecretKey(key))
  const clientId = clientIdEntry?.[1]?.trim() ?? ""
  const clientSecret = clientSecretEntry?.[1]?.trim() ?? ""
  if (!clientId) {
    throw new Error("OAuth client ID is required")
  }
  return { clientId, clientSecret: clientSecret || undefined }
}

function catalogMcpProviderId(
  entry: PlatformMCPCatalogRead,
  optionId: string | null | undefined
) {
  const suffix = optionId ? `-${optionId}` : ""
  return `custom_mcp_${entry.slug}${suffix}`.replace(/[^a-zA-Z0-9_]+/g, "_")
}

function CatalogEntrySummary({ entry }: { entry: PlatformMCPCatalogRead }) {
  const providerId = entry.slug.replace(/-/g, "_")
  const transports = Array.from(
    new Set(
      (entry.connection_options?.length
        ? entry.connection_options.map((option) =>
            transportLabel(option.connection_spec)
          )
        : [transportLabel(entry.connection_spec)]
      ).filter(Boolean)
    )
  )

  return (
    <div className="flex items-start gap-3 rounded-md border bg-muted/30 p-3">
      <ProviderIcon providerId={providerId} className="size-9 shrink-0" />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex min-w-0 items-center gap-2">
          <div className="truncate text-sm font-medium text-foreground">
            {entry.name}
          </div>
          {transports.map((transport) => (
            <Badge
              key={transport}
              variant="outline"
              className="h-4 px-1.5 text-[10px] uppercase tracking-wide"
            >
              {transport}
            </Badge>
          ))}
        </div>
        <p className="line-clamp-2 text-xs leading-5 text-muted-foreground">
          {entry.description}
        </p>
        {entry.docs_url ? (
          <a
            href={entry.docs_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex w-fit items-center gap-1 text-xs text-blue-600 hover:underline"
          >
            View docs
            <ExternalLink className="size-3" />
          </a>
        ) : null}
      </div>
    </div>
  )
}

export function MCPIntegrationDialog({
  triggerProps,
  mcpIntegrationId,
  onOpenChange,
  onSaved,
  open: controlledOpen,
  hideTrigger = false,
  catalogEntry,
}: {
  triggerProps?: ButtonProps
  mcpIntegrationId?: string | null
  onOpenChange?: (open: boolean) => void
  onSaved?: () => void
  open?: boolean
  hideTrigger?: boolean
  /**
   * When opening from the MCP servers catalog, prefill the create form from the
   * selected entry's connection metadata. Ignored in edit mode.
   */
  catalogEntry?: PlatformMCPCatalogRead | null
}) {
  const workspaceId = useWorkspaceId()
  const isEditMode = Boolean(mcpIntegrationId)
  const { connectMcpIntegration, connectMcpIntegrationIsPending } =
    useConnectMcpIntegration(workspaceId)
  const { createMcpIntegration, createMcpIntegrationIsPending } =
    useCreateMcpIntegration(workspaceId)
  const { updateMcpIntegration, updateMcpIntegrationIsPending } =
    useUpdateMcpIntegration(workspaceId)
  const { deleteMcpIntegration, deleteMcpIntegrationIsPending } =
    useDeleteMcpIntegration(workspaceId)
  const { integrations, providers, integrationsIsLoading } =
    useIntegrations(workspaceId)
  const { mcpIntegration, mcpIntegrationIsLoading } = useGetMcpIntegration(
    workspaceId,
    mcpIntegrationId ?? null
  )
  const canDelete = useScopeCheck("integration:delete") === true
  const [internalOpen, setInternalOpen] = useState(false)
  const [isEditHydrated, setIsEditHydrated] = useState(false)
  const [catalogOAuthClientIsPending, setCatalogOAuthClientIsPending] =
    useState(false)
  const open = controlledOpen ?? internalOpen
  const { className: triggerClassName, ...restTriggerProps } =
    triggerProps ?? {}

  // Open dialog when mcpIntegrationId is set (edit mode)
  React.useEffect(() => {
    if (mcpIntegrationId && controlledOpen === undefined) {
      setInternalOpen(true)
    }
  }, [mcpIntegrationId, controlledOpen])

  const form = useForm<MCPIntegrationFormValues>({
    resolver: zodResolver(mcpIntegrationFormSchema),
    defaultValues: MCP_INTEGRATION_FORM_DEFAULTS,
  })
  const {
    fields: stdioArgFields,
    append: appendStdioArg,
    remove: removeStdioArg,
    replace: replaceStdioArgs,
  } = useFieldArray({
    control: form.control,
    name: "stdio_args",
  })

  const serverType = form.watch("server_type")
  const authType = form.watch("auth_type")
  const oauthSetup = form.watch("oauth_setup")
  const connectionOptionId = form.watch("connection_option_id")
  const selectedCatalogSpec = catalogSpecForOption(
    catalogEntry,
    connectionOptionId
  )
  const hasCatalogOAuthClient = hasOAuthClientConfig(selectedCatalogSpec)
  const catalogOptions = catalogEntry?.connection_options ?? []
  const connectedOAuthIntegrations =
    integrations?.filter(
      (int) => int.status === "connected" && !isMcpProvider(int.provider_id)
    ) ?? []

  React.useEffect(() => {
    if (!isEditMode) {
      setIsEditHydrated(true)
      return
    }
    if (open) {
      setIsEditHydrated(false)
    }
  }, [isEditMode, open, mcpIntegrationId])

  // Load integration data when in edit mode
  React.useEffect(() => {
    if (isEditMode && mcpIntegration && !mcpIntegrationIsLoading) {
      if (mcpIntegration.id !== mcpIntegrationId) {
        return
      }
      const hydratedStdioArgs =
        mcpIntegration.stdio_args?.map((arg) => ({ value: arg })) || []
      form.reset({
        name: mcpIntegration.name,
        description: mcpIntegration.description || "",
        server_type: mcpIntegration.server_type,
        server_uri: mcpIntegration.server_uri || "",
        auth_type: mcpIntegration.auth_type,
        oauth_setup: mcpIntegration.oauth_integration_id
          ? "existing_integration"
          : "mcp_discovery",
        oauth_integration_id: mcpIntegration.oauth_integration_id || "",
        oauth_client_credentials: "",
        custom_credentials: "", // Don't populate for security
        stdio_command: mcpIntegration.stdio_command || "",
        stdio_args: hydratedStdioArgs,
        stdio_env: "", // Env vars are not returned from API for security
        timeout: mcpIntegration.timeout || 30,
        catalog_slug: catalogEntry?.slug || "",
        connection_option_id: catalogOptionIdForIntegration(
          catalogEntry,
          mcpIntegration
        ),
      })
      // Explicitly sync field-array state; reset() can lag on first mount.
      replaceStdioArgs(hydratedStdioArgs)
      setIsEditHydrated(true)
    }
  }, [
    isEditMode,
    mcpIntegration,
    mcpIntegrationIsLoading,
    mcpIntegrationId,
    catalogEntry,
    form,
    replaceStdioArgs,
  ])

  // Prefill from a catalog entry when opening in create mode. Keyed on the
  // entry slug so re-opening with a different server re-seeds the form.
  React.useEffect(() => {
    if (isEditMode || !open || !catalogEntry) {
      return
    }
    const defaultOptionId = catalogEntry.connection_options?.[0]?.id
    const values = catalogEntryToFormValues(catalogEntry, defaultOptionId)
    form.reset(values)
    replaceStdioArgs(values.stdio_args)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isEditMode, open, catalogEntry?.slug])

  const resetForm = () => {
    if (
      isEditMode &&
      mcpIntegration &&
      mcpIntegration.id === mcpIntegrationId
    ) {
      const hydratedStdioArgs =
        mcpIntegration.stdio_args?.map((arg) => ({ value: arg })) || []
      form.reset({
        name: mcpIntegration.name,
        description: mcpIntegration.description || "",
        server_type: mcpIntegration.server_type,
        server_uri: mcpIntegration.server_uri || "",
        auth_type: mcpIntegration.auth_type,
        oauth_setup: mcpIntegration.oauth_integration_id
          ? "existing_integration"
          : "mcp_discovery",
        oauth_integration_id: mcpIntegration.oauth_integration_id || "",
        oauth_client_credentials: "",
        custom_credentials: "",
        stdio_command: mcpIntegration.stdio_command || "",
        stdio_args: hydratedStdioArgs,
        stdio_env: "", // Env vars are not returned from API for security
        timeout: mcpIntegration.timeout || 30,
        catalog_slug: catalogEntry?.slug || "",
        connection_option_id: catalogOptionIdForIntegration(
          catalogEntry,
          mcpIntegration
        ),
      })
      replaceStdioArgs(hydratedStdioArgs)
      setIsEditHydrated(true)
    } else {
      form.reset(MCP_INTEGRATION_FORM_DEFAULTS)
      replaceStdioArgs([])
      setIsEditHydrated(false)
    }
  }

  const handleOpenChange = (nextOpen: boolean) => {
    if (controlledOpen === undefined) {
      setInternalOpen(nextOpen)
    }
    onOpenChange?.(nextOpen)
    if (!nextOpen) {
      resetForm()
      setIsEditHydrated(false)
    }
  }

  const onSubmit = async (values: MCPIntegrationFormValues) => {
    let hookHandledError = false
    try {
      // Parse stdio_args list to string array
      const stdioArgs = values.stdio_args
        .map((arg) => arg.value.trim())
        .filter(Boolean)

      // Parse stdio_env from JSON string to object (only for stdio-type servers)
      const stdioEnv =
        values.server_type === "stdio" && values.stdio_env?.trim()
          ? (JSON.parse(values.stdio_env) as Record<string, string>)
          : undefined

      const baseParams = {
        name: values.name.trim(),
        description: values.description?.trim() || undefined,
        timeout: values.timeout,
      }
      const createBaseParams = {
        ...baseParams,
        catalog_slug: values.catalog_slug || undefined,
      }
      const trimmedCustomCredentials = values.custom_credentials?.trim() ?? ""
      const customCredentialsWasEdited = Boolean(
        form.formState.dirtyFields.custom_credentials
      )

      let customCredentialsForCreate: string | undefined
      if (values.auth_type !== "NONE" && trimmedCustomCredentials !== "") {
        customCredentialsForCreate = trimmedCustomCredentials
      }

      let customCredentialsForUpdate: string | undefined
      if (values.auth_type !== "NONE") {
        if (trimmedCustomCredentials !== "") {
          customCredentialsForUpdate = trimmedCustomCredentials
        } else if (customCredentialsWasEdited) {
          // Explicitly send an empty string when a user clears the editor in edit mode.
          customCredentialsForUpdate = ""
        }
      }

      if (isEditMode && mcpIntegrationId) {
        const params: MCPIntegrationUpdate =
          values.server_type === "stdio"
            ? {
                ...baseParams,
                server_type: "stdio",
                stdio_command: values.stdio_command?.trim() ?? "",
                stdio_args: stdioArgs,
                stdio_env: stdioEnv,
              }
            : {
                ...baseParams,
                server_type: "http",
                server_uri: values.server_uri?.trim() ?? "",
                auth_type: values.auth_type,
                oauth_integration_id:
                  values.auth_type === "OAUTH2" &&
                  values.oauth_setup === "existing_integration" &&
                  values.oauth_integration_id
                    ? values.oauth_integration_id
                    : null,
                custom_credentials: customCredentialsForUpdate,
              }
        hookHandledError = true
        await updateMcpIntegration({
          mcpIntegrationId,
          params,
        })
        hookHandledError = false
      } else {
        if (values.server_type === "stdio") {
          const params: MCPStdioIntegrationCreate = {
            ...createBaseParams,
            server_type: "stdio",
            stdio_command: values.stdio_command?.trim() ?? "",
            stdio_args: stdioArgs.length > 0 ? stdioArgs : undefined,
            stdio_env: stdioEnv,
          }
          hookHandledError = true
          await createMcpIntegration(params)
          hookHandledError = false
        } else {
          let params: MCPHttpIntegrationCreate = {
            ...createBaseParams,
            server_type: "http",
            server_uri: values.server_uri?.trim() ?? "",
            auth_type: values.auth_type,
            oauth_integration_id:
              values.auth_type === "OAUTH2" &&
              values.oauth_setup === "existing_integration" &&
              values.oauth_integration_id
                ? values.oauth_integration_id
                : undefined,
            custom_credentials: customCredentialsForCreate,
          }
          if (
            values.auth_type === "OAUTH2" &&
            values.oauth_setup === "oauth_client"
          ) {
            if (!catalogEntry) {
              throw new Error(
                "Catalog entry is required for OAuth client setup"
              )
            }
            const spec = catalogSpecForOption(
              catalogEntry,
              values.connection_option_id
            )
            if (spec?.server_type !== "http" || spec.auth_type !== "OAUTH2") {
              throw new Error(
                "Catalog OAuth client setup requires an HTTP OAuth option"
              )
            }
            const oauthClientCredentials =
              values.oauth_client_credentials?.trim() ?? ""
            // Block submission when the spec marks OAuth client credentials as
            // required but the pasted JSON still has empty values; otherwise an
            // empty client_secret is silently dropped and the OAuth callback
            // token exchange fails.
            const missingCredentials = missingRequiredOAuthClientCredentials(
              spec,
              oauthClientCredentials
            )
            if (missingCredentials.length > 0) {
              form.setError("oauth_client_credentials", {
                type: "manual",
                message: `Missing required values: ${missingCredentials.join(", ")}`,
              })
              return
            }
            setCatalogOAuthClientIsPending(true)
            // Without advertised endpoints, the backend does dynamic registration
            // from the pasted credentials; otherwise create the OAuth client here.
            let result: MCPCatalogConnectResponse
            if (
              !spec.oauth_authorization_endpoint ||
              !spec.oauth_token_endpoint
            ) {
              hookHandledError = true
              result = await connectMcpIntegration({
                ...params,
                custom_credentials: oauthClientCredentials,
              })
              hookHandledError = false
            } else {
              const { clientId, clientSecret } = readOAuthClientCredentials(
                oauthClientCredentials
              )
              const provider = await providersCreateCustomProvider({
                workspaceId,
                requestBody: {
                  provider_id: catalogMcpProviderId(
                    catalogEntry,
                    values.connection_option_id
                  ),
                  name: `${values.name} OAuth`,
                  description:
                    values.description?.trim() ||
                    `OAuth client for ${values.name}`,
                  grant_type: "authorization_code",
                  authorization_endpoint: spec.oauth_authorization_endpoint,
                  token_endpoint: spec.oauth_token_endpoint,
                  scopes: spec.scopes ?? [],
                  client_id: clientId,
                  client_secret: clientSecret,
                },
              })
              const oauthIntegration = await integrationsGetIntegration({
                workspaceId,
                providerId: provider.id,
                grantType: "authorization_code",
              })
              params = {
                ...params,
                oauth_integration_id: oauthIntegration.id,
              }
              hookHandledError = true
              await createMcpIntegration(params)
              hookHandledError = false
              result = await mcpIntegrationsConnectPlatformMcpCatalog({
                workspaceId,
                catalogSlug: catalogEntry.slug,
              })
            }
            if (result.auth_url) {
              window.location.href = result.auth_url
              return
            }
            setCatalogOAuthClientIsPending(false)
          } else if (
            values.auth_type === "OAUTH2" &&
            values.oauth_setup === "mcp_discovery" &&
            !params.oauth_integration_id
          ) {
            hookHandledError = true
            const result = await connectMcpIntegration(params)
            hookHandledError = false
            if (result.auth_url) {
              window.location.href = result.auth_url
              return
            }
          } else {
            hookHandledError = true
            await createMcpIntegration(params)
            hookHandledError = false
          }
        }
      }
      onSaved?.()
      handleOpenChange(false)
    } catch (error) {
      setCatalogOAuthClientIsPending(false)
      // Error is handled by the hook's onError callback
      console.error(
        `Failed to ${isEditMode ? "update" : "create"} MCP integration:`,
        error
      )
      if (!hookHandledError) {
        toast({
          title: `Failed to ${isEditMode ? "update" : "create"} MCP integration`,
          description: getMcpOAuthConnectErrorDetail(error),
          variant: "destructive",
        })
      }
    }
  }

  const isPending =
    catalogOAuthClientIsPending ||
    connectMcpIntegrationIsPending ||
    createMcpIntegrationIsPending ||
    updateMcpIntegrationIsPending
  const dialogTitle = catalogEntry
    ? `Configure ${catalogEntry.name}`
    : isEditMode
      ? "Edit MCP server"
      : "Add MCP server"
  const dialogDescription = catalogEntry
    ? "Review the catalog defaults and fill in any workspace-specific values."
    : isEditMode
      ? "Update how this MCP server connects from this workspace."
      : "Configure a custom MCP server for this workspace."

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      {!isEditMode && !hideTrigger && (
        <DialogTrigger asChild>
          <Button
            size="sm"
            variant="outline"
            className={cn("h-7 bg-white", triggerClassName)}
            {...restTriggerProps}
          >
            <Plus className="mr-1 h-3.5 w-3.5" />
            Add MCP Integration
          </Button>
        </DialogTrigger>
      )}
      <DialogContent className="max-h-[88vh] max-w-2xl overflow-hidden p-0">
        <DialogHeader className="px-6 pt-6 text-left">
          <DialogTitle>{dialogTitle}</DialogTitle>
          <DialogDescription>{dialogDescription}</DialogDescription>
        </DialogHeader>
        <div className="max-h-[calc(88vh-92px)] overflow-y-auto px-6 py-5">
          {catalogEntry ? (
            <div className="mb-6">
              <CatalogEntrySummary entry={catalogEntry} />
            </div>
          ) : null}
          {(integrationsIsLoading ||
            (isEditMode &&
              (!isEditHydrated ||
                mcpIntegrationIsLoading ||
                mcpIntegration?.id !== mcpIntegrationId))) && (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
            </div>
          )}
          {!integrationsIsLoading &&
            (!isEditMode ||
              (isEditHydrated &&
                !mcpIntegrationIsLoading &&
                mcpIntegration?.id === mcpIntegrationId)) && (
              <Form {...form}>
                <form
                  className="space-y-5"
                  onSubmit={form.handleSubmit(onSubmit)}
                  noValidate
                >
                  {catalogEntry && catalogOptions.length > 1 ? (
                    <FormField
                      control={form.control}
                      name="connection_option_id"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Connection option</FormLabel>
                          <FormControl>
                            <div className="grid gap-2 sm:grid-cols-2">
                              {catalogOptions.map((option) => {
                                const Icon = optionIcon(option.connection_spec)
                                const selected = field.value === option.id
                                return (
                                  <button
                                    key={option.id}
                                    type="button"
                                    className={cn(
                                      "flex min-h-20 items-start gap-3 rounded-md border bg-background p-3 text-left transition-colors hover:border-foreground/30",
                                      selected &&
                                        "border-blue-500 bg-blue-50/60 ring-1 ring-blue-500"
                                    )}
                                    onClick={() => {
                                      if (selected) {
                                        return
                                      }
                                      const values = catalogEntryToFormValues(
                                        catalogEntry,
                                        option.id
                                      )
                                      if (isEditMode) {
                                        const currentValues = form.getValues()
                                        const nextValues = {
                                          ...values,
                                          name: currentValues.name,
                                          description:
                                            currentValues.description,
                                          timeout: currentValues.timeout,
                                          catalog_slug:
                                            currentValues.catalog_slug ||
                                            values.catalog_slug,
                                        }
                                        form.reset(nextValues)
                                        replaceStdioArgs(nextValues.stdio_args)
                                      } else {
                                        form.reset(values)
                                        replaceStdioArgs(values.stdio_args)
                                      }
                                    }}
                                  >
                                    <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md border bg-muted/40">
                                      <Icon className="size-4 text-muted-foreground" />
                                    </span>
                                    <span className="min-w-0 flex-1">
                                      <span className="flex items-center gap-2">
                                        <span className="text-sm font-medium text-foreground">
                                          {option.label}
                                        </span>
                                        {transportLabel(
                                          option.connection_spec
                                        ) ? (
                                          <Badge
                                            variant="outline"
                                            className="h-4 px-1.5 text-[10px] uppercase tracking-wide"
                                          >
                                            {transportLabel(
                                              option.connection_spec
                                            )}
                                          </Badge>
                                        ) : null}
                                        {selected ? (
                                          <Check className="ml-auto size-4 text-blue-600" />
                                        ) : null}
                                      </span>
                                      {option.description ? (
                                        <span className="mt-1 block text-xs leading-5 text-muted-foreground">
                                          {option.description}
                                        </span>
                                      ) : null}
                                    </span>
                                  </button>
                                )
                              })}
                            </div>
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  ) : null}

                  <FormField
                    control={form.control}
                    name="name"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Integration name</FormLabel>
                        <FormControl>
                          <Input placeholder="My MCP Server" {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="description"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Description</FormLabel>
                        <FormControl>
                          <Textarea
                            placeholder="Optional description for this MCP integration"
                            {...field}
                          />
                        </FormControl>
                        <FormDescription className="text-xs">
                          Appears in the integrations list for this workspace.
                        </FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  {!catalogEntry ? (
                    <FormField
                      control={form.control}
                      name="server_type"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Server type</FormLabel>
                          <FormControl>
                            <Select
                              value={field.value}
                              onValueChange={field.onChange}
                              disabled={isEditMode}
                            >
                              <SelectTrigger>
                                <SelectValue placeholder="Select server type">
                                  {field.value
                                    ? SERVER_TYPES.find(
                                        (opt) => opt.value === field.value
                                      )?.label
                                    : null}
                                </SelectValue>
                              </SelectTrigger>
                              <SelectContent>
                                {SERVER_TYPES.map((option) => (
                                  <SelectItem
                                    key={option.value}
                                    value={option.value}
                                    textValue={option.label}
                                  >
                                    <div className="flex flex-col gap-1">
                                      <span className="text-sm font-medium">
                                        {option.label}
                                      </span>
                                      <span className="text-xs text-muted-foreground">
                                        {option.description}
                                      </span>
                                    </div>
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </FormControl>
                          <FormDescription className="text-xs">
                            {isEditMode
                              ? "Server type cannot be changed after creation."
                              : "Choose how to connect to the MCP server."}
                          </FormDescription>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  ) : null}

                  {/* HTTP-type fields */}
                  {serverType === "http" && (
                    <>
                      <FormField
                        control={form.control}
                        name="server_uri"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Server URI</FormLabel>
                            <FormControl>
                              <Input
                                placeholder="https://mcp.example.com/mcp"
                                {...field}
                              />
                            </FormControl>
                            <FormDescription className="text-xs">
                              The MCP server endpoint URL. Must use HTTPS
                              (localhost allowed with HTTP).
                            </FormDescription>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                      {!catalogEntry ? (
                        <FormField
                          control={form.control}
                          name="auth_type"
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>Authentication type</FormLabel>
                              <FormControl>
                                <Select
                                  value={field.value}
                                  onValueChange={field.onChange}
                                >
                                  <SelectTrigger>
                                    <SelectValue placeholder="Select authentication type">
                                      {field.value
                                        ? AUTH_TYPES.find(
                                            (opt) => opt.value === field.value
                                          )?.label
                                        : null}
                                    </SelectValue>
                                  </SelectTrigger>
                                  <SelectContent>
                                    {AUTH_TYPES.map((option) => (
                                      <SelectItem
                                        key={option.value}
                                        value={option.value}
                                        textValue={option.label}
                                      >
                                        <div className="flex flex-col gap-1">
                                          <span className="text-sm font-medium">
                                            {option.label}
                                          </span>
                                          <span className="text-xs text-muted-foreground">
                                            {option.description}
                                          </span>
                                        </div>
                                      </SelectItem>
                                    ))}
                                  </SelectContent>
                                </Select>
                              </FormControl>
                              <FormDescription className="text-xs">
                                Choose how to authenticate with the MCP server.
                              </FormDescription>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                      ) : null}
                    </>
                  )}

                  {/* Stdio-type fields */}
                  {serverType === "stdio" && (
                    <>
                      <FormField
                        control={form.control}
                        name="stdio_command"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Stdio command</FormLabel>
                            <FormControl>
                              {(() => {
                                const currentCommand = (
                                  field.value || ""
                                ).trim()
                                const hasLegacyValue =
                                  currentCommand !== "" &&
                                  !isAllowedCommand(currentCommand)
                                const selectedValue = hasLegacyValue
                                  ? "__legacy__"
                                  : currentCommand
                                return (
                                  <Select
                                    value={selectedValue}
                                    onValueChange={(value) =>
                                      field.onChange(
                                        value === "__legacy__"
                                          ? currentCommand
                                          : value
                                      )
                                    }
                                  >
                                    <SelectTrigger>
                                      <SelectValue placeholder="Select stdio command">
                                        {hasLegacyValue
                                          ? `Legacy (${currentCommand})`
                                          : currentCommand || null}
                                      </SelectValue>
                                    </SelectTrigger>
                                    <SelectContent>
                                      {hasLegacyValue && (
                                        <SelectItem value="__legacy__">
                                          Legacy ({currentCommand})
                                        </SelectItem>
                                      )}
                                      {ALLOWED_COMMANDS.map((cmd) => (
                                        <SelectItem key={cmd} value={cmd}>
                                          {cmd}
                                        </SelectItem>
                                      ))}
                                    </SelectContent>
                                  </Select>
                                )
                              })()}
                            </FormControl>
                            <FormDescription className="text-xs">
                              Stdio command to run the MCP server. Only{" "}
                              {ALLOWED_COMMANDS.join(", ")} are allowed.
                            </FormDescription>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                      <FormField
                        control={form.control}
                        name="stdio_args"
                        render={() => (
                          <FormItem>
                            <FormLabel>Stdio arguments</FormLabel>
                            <div className="space-y-2">
                              {stdioArgFields.length === 0 && (
                                <p className="text-xs text-muted-foreground">
                                  No arguments configured.
                                </p>
                              )}
                              {stdioArgFields.map((argField, index) => (
                                <div
                                  key={argField.id}
                                  className="grid grid-cols-[1fr_auto] gap-2"
                                >
                                  <FormField
                                    control={form.control}
                                    name={`stdio_args.${index}.value`}
                                    render={({ field }) => (
                                      <FormItem>
                                        <FormControl>
                                          <Input
                                            {...field}
                                            placeholder={
                                              index === 0
                                                ? "@modelcontextprotocol/server-github"
                                                : "Additional argument"
                                            }
                                            className="font-mono text-xs"
                                          />
                                        </FormControl>
                                        <FormMessage />
                                      </FormItem>
                                    )}
                                  />
                                  <Button
                                    type="button"
                                    variant="ghost"
                                    size="icon"
                                    onClick={() => removeStdioArg(index)}
                                  >
                                    <Trash2 className="size-4" />
                                    <span className="sr-only">
                                      Remove argument
                                    </span>
                                  </Button>
                                </div>
                              ))}
                              <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                onClick={() => appendStdioArg({ value: "" })}
                              >
                                <Plus className="mr-2 size-4" />
                                Add argument
                              </Button>
                            </div>
                            <FormDescription className="text-xs">
                              Add each command argument as a separate list item,
                              in order.
                            </FormDescription>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                      <FormField
                        control={form.control}
                        name="stdio_env"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Stdio environment variables</FormLabel>
                            <FormControl>
                              <CodeEditor
                                value={field.value || ""}
                                onChange={field.onChange}
                                language="json"
                                className="font-mono text-xs [&_.cm-content]:text-xs [&_.cm-editor]:min-h-[80px]"
                              />
                            </FormControl>
                            <FormDescription className="text-xs">
                              JSON object with environment variables for the
                              stdio command. Template expressions are supported,
                              for example{" "}
                              <code>
                                {
                                  '{"GITHUB_TOKEN": "${{ SECRETS.github.TOKEN }}"}'
                                }
                              </code>
                              .
                            </FormDescription>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </>
                  )}

                  <Accordion type="single" collapsible>
                    <AccordionItem value="advanced" className="border-b-0">
                      <AccordionTrigger className="py-3 hover:no-underline">
                        Advanced
                      </AccordionTrigger>
                      <AccordionContent className="space-y-5">
                        {serverType === "http" && authType === "OAUTH2" ? (
                          <FormField
                            control={form.control}
                            name="oauth_setup"
                            render={({ field }) => (
                              <FormItem>
                                <FormLabel>OAuth flow</FormLabel>
                                <FormControl>
                                  <Select
                                    value={field.value}
                                    onValueChange={(value) => {
                                      field.onChange(value)
                                      if (value === "mcp_discovery") {
                                        form.setValue(
                                          "oauth_integration_id",
                                          "",
                                          { shouldDirty: true }
                                        )
                                        form.setValue(
                                          "oauth_client_credentials",
                                          "",
                                          { shouldDirty: true }
                                        )
                                      }
                                    }}
                                  >
                                    <SelectTrigger>
                                      <SelectValue placeholder="Select OAuth flow">
                                        {field.value === "existing_integration"
                                          ? "Tracecat OAuth integration"
                                          : field.value === "oauth_client"
                                            ? "OAuth client credentials"
                                            : "MCP OAuth discovery"}
                                      </SelectValue>
                                    </SelectTrigger>
                                    <SelectContent>
                                      {hasCatalogOAuthClient ? (
                                        <SelectItem
                                          value="oauth_client"
                                          textValue="OAuth client credentials"
                                        >
                                          <div className="flex flex-col gap-1">
                                            <span className="text-sm font-medium">
                                              OAuth client credentials
                                            </span>
                                            <span className="text-xs text-muted-foreground">
                                              Create a Tracecat OAuth
                                              integration from this client ID
                                              and secret.
                                            </span>
                                          </div>
                                        </SelectItem>
                                      ) : null}
                                      <SelectItem
                                        value="mcp_discovery"
                                        textValue="MCP OAuth discovery"
                                      >
                                        <div className="flex flex-col gap-1">
                                          <span className="text-sm font-medium">
                                            MCP OAuth discovery
                                          </span>
                                          <span className="text-xs text-muted-foreground">
                                            Discover endpoints and register a
                                            client from the MCP server.
                                          </span>
                                        </div>
                                      </SelectItem>
                                      <SelectItem
                                        value="existing_integration"
                                        textValue="Tracecat OAuth integration"
                                      >
                                        <div className="flex flex-col gap-1">
                                          <span className="text-sm font-medium">
                                            Tracecat OAuth integration
                                          </span>
                                          <span className="text-xs text-muted-foreground">
                                            Use an OAuth integration with a
                                            known client ID and secret.
                                          </span>
                                        </div>
                                      </SelectItem>
                                    </SelectContent>
                                  </Select>
                                </FormControl>
                                <FormDescription className="text-xs">
                                  Use discovery by default. Switch only when the
                                  MCP server needs a preconfigured OAuth client.
                                </FormDescription>
                                <FormMessage />
                              </FormItem>
                            )}
                          />
                        ) : null}

                        {serverType === "http" &&
                        authType === "OAUTH2" &&
                        oauthSetup === "oauth_client" ? (
                          <FormField
                            control={form.control}
                            name="oauth_client_credentials"
                            render={({ field }) => (
                              <FormItem>
                                <FormLabel>
                                  OAuth client credentials (JSON)
                                </FormLabel>
                                <FormControl>
                                  <CodeEditor
                                    value={field.value || ""}
                                    onChange={field.onChange}
                                    language="json"
                                    className="font-mono text-xs [&_.cm-content]:text-xs [&_.cm-editor]:min-h-[96px]"
                                  />
                                </FormControl>
                                <FormDescription className="text-xs">
                                  Enter the client ID and optional client secret
                                  from the provider OAuth app.
                                </FormDescription>
                                <FormMessage />
                              </FormItem>
                            )}
                          />
                        ) : null}

                        {serverType === "http" &&
                        authType === "OAUTH2" &&
                        oauthSetup === "existing_integration" ? (
                          <FormField
                            control={form.control}
                            name="oauth_integration_id"
                            render={({ field }) => (
                              <FormItem>
                                <FormLabel>OAuth integration</FormLabel>
                                <FormControl>
                                  <Select
                                    value={field.value || ""}
                                    onValueChange={field.onChange}
                                  >
                                    <SelectTrigger>
                                      <SelectValue placeholder="Select OAuth integration" />
                                    </SelectTrigger>
                                    <SelectContent>
                                      {integrationsIsLoading ? (
                                        <div className="px-2 py-1.5 text-xs text-muted-foreground">
                                          Loading integrations...
                                        </div>
                                      ) : connectedOAuthIntegrations.length >
                                        0 ? (
                                        connectedOAuthIntegrations.map(
                                          (integration) => {
                                            const provider = providers?.find(
                                              (p) =>
                                                p.id === integration.provider_id
                                            )
                                            return (
                                              <SelectItem
                                                key={integration.id}
                                                value={integration.id}
                                                textValue={
                                                  provider?.name ||
                                                  integration.provider_id
                                                }
                                              >
                                                <div className="flex items-center gap-2">
                                                  {provider ? (
                                                    <ProviderIcon
                                                      providerId={
                                                        integration.provider_id
                                                      }
                                                      className="size-4 shrink-0 bg-transparent p-0"
                                                    />
                                                  ) : null}
                                                  <span className="text-sm font-medium">
                                                    {provider?.name ||
                                                      integration.provider_id}
                                                  </span>
                                                </div>
                                              </SelectItem>
                                            )
                                          }
                                        )
                                      ) : (
                                        <div className="px-2 py-1.5 text-xs text-muted-foreground">
                                          No OAuth integrations available.
                                        </div>
                                      )}
                                    </SelectContent>
                                  </Select>
                                </FormControl>
                                <FormDescription className="text-xs">
                                  Select a connected OAuth integration from this
                                  workspace.
                                </FormDescription>
                                <FormMessage />
                              </FormItem>
                            )}
                          />
                        ) : null}

                        {serverType === "http" && authType === "OAUTH2" ? (
                          <FormField
                            control={form.control}
                            name="custom_credentials"
                            render={({ field }) => (
                              <FormItem>
                                <FormLabel>Additional headers (JSON)</FormLabel>
                                <FormControl>
                                  <CodeEditor
                                    value={field.value || ""}
                                    onChange={field.onChange}
                                    language="json"
                                    className="font-mono text-xs [&_.cm-content]:text-xs [&_.cm-editor]:min-h-[120px]"
                                  />
                                </FormControl>
                                <FormDescription className="text-xs">
                                  Authorization is set from OAuth and cannot be
                                  overridden.
                                </FormDescription>
                                <FormMessage />
                              </FormItem>
                            )}
                          />
                        ) : null}

                        <FormField
                          control={form.control}
                          name="timeout"
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>Timeout (seconds)</FormLabel>
                              <FormControl>
                                <Input
                                  type="number"
                                  min={1}
                                  max={300}
                                  placeholder="30"
                                  {...field}
                                />
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                      </AccordionContent>
                    </AccordionItem>
                  </Accordion>

                  {serverType === "http" && authType === "CUSTOM" && (
                    <FormField
                      control={form.control}
                      name="custom_credentials"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Headers / API key (JSON)</FormLabel>
                          <FormControl>
                            <CodeEditor
                              value={field.value || ""}
                              onChange={field.onChange}
                              language="json"
                              className="font-mono text-xs [&_.cm-content]:text-xs [&_.cm-editor]:min-h-[120px]"
                            />
                          </FormControl>
                          <FormDescription className="text-xs">
                            Enter headers as a JSON object, for example{" "}
                            <code>{`{"Authorization":"Bearer token123"}`}</code>
                          </FormDescription>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  )}

                  <DialogFooter className="flex flex-col gap-2 sm:flex-row sm:justify-between">
                    {isEditMode && mcpIntegrationId && canDelete ? (
                      <AlertDialog>
                        <AlertDialogTrigger asChild>
                          <Button
                            type="button"
                            variant="outline"
                            className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                            disabled={
                              isPending || deleteMcpIntegrationIsPending
                            }
                          >
                            <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                            Delete
                          </Button>
                        </AlertDialogTrigger>
                        <AlertDialogContent>
                          <AlertDialogHeader>
                            <AlertDialogTitle>
                              Remove MCP server?
                            </AlertDialogTitle>
                            <AlertDialogDescription>
                              Agents will no longer be able to call{" "}
                              <span className="font-medium">
                                {mcpIntegration?.name ?? "this server"}
                              </span>
                              .
                            </AlertDialogDescription>
                          </AlertDialogHeader>
                          <AlertDialogFooter>
                            <AlertDialogCancel>Cancel</AlertDialogCancel>
                            <AlertDialogAction
                              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                              disabled={deleteMcpIntegrationIsPending}
                              onClick={async (event) => {
                                event.preventDefault()
                                await deleteMcpIntegration(mcpIntegrationId)
                                handleOpenChange(false)
                              }}
                            >
                              {deleteMcpIntegrationIsPending && (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                              )}
                              Remove
                            </AlertDialogAction>
                          </AlertDialogFooter>
                        </AlertDialogContent>
                      </AlertDialog>
                    ) : (
                      <span />
                    )}
                    <div className="flex items-center justify-end gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => handleOpenChange(false)}
                        disabled={isPending}
                      >
                        Cancel
                      </Button>
                      <Button
                        type="submit"
                        className="gap-2"
                        disabled={isPending}
                      >
                        {isPending && (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        )}
                        {isEditMode ? "Update" : "Connect"}
                      </Button>
                    </div>
                  </DialogFooter>
                </form>
              </Form>
            )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
