"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Loader2 } from "lucide-react"
import { type ReactNode, useEffect, useMemo, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import {
  type AgentCatalogRead,
  type AgentCustomProviderCreate,
  type AgentCustomProviderRead,
  type AgentCustomProviderUpdate,
  type AgentModelAccessRead,
  type ApiError,
  type AzureAICatalogCreate,
  type AzureOpenAICatalogCreate,
  agentDeleteProviderCredentials,
  type BedrockCatalogCreate,
  createCatalogEntry,
  createCustomProvider,
  deleteCatalogEntry,
  deleteCustomProvider,
  disableModel,
  enableModel,
  listCatalog,
  listCustomProviders,
  listEnabledModels,
  refreshCustomProviderCatalog,
  updateCatalogEntry,
  updateCustomProvider,
  type VertexAICatalogCreate,
  validateCustomProviderConnection,
} from "@/client"
import { ProviderIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { AgentCredentialsDialog } from "@/components/organization/org-agent-credentials-dialog"
import {
  RbacListContainer,
  RbacListItem,
} from "@/components/organization/rbac-list-item"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
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
import { ScrollArea } from "@/components/ui/scroll-area"
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
import { useDebounce } from "@/hooks"
import { useEntitlements } from "@/hooks/use-entitlements"
import { getApiErrorDetail, retryHandler } from "@/lib/errors"
import {
  useAgentDefaultModel,
  useModelProvidersStatus,
  useProviderCredentialConfigs,
} from "@/lib/hooks"
import { cn } from "@/lib/utils"

const CURSOR_PAGE_SIZE = 100

const customProviderSchema = z
  .object({
    displayName: z.string().trim().min(1, "Name is required"),
    baseUrl: z.union([z.string().url(), z.literal(""), z.undefined()]),
    apiKeyHeader: z.string().trim().optional(),
    apiKey: z.string().optional(),
    customHeadersJson: z.string().optional(),
  })
  .superRefine((value, ctx) => {
    const raw = value.customHeadersJson?.trim()
    if (!raw) {
      return
    }

    try {
      const parsed = JSON.parse(raw)
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Custom headers must be a JSON object.",
          path: ["customHeadersJson"],
        })
        return
      }
      for (const [key, headerValue] of Object.entries(parsed)) {
        if (typeof key !== "string" || typeof headerValue !== "string") {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message: "Custom headers must map string keys to string values.",
            path: ["customHeadersJson"],
          })
          return
        }
      }
    } catch {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Custom headers must be valid JSON.",
        path: ["customHeadersJson"],
      })
    }
  })

type CustomProviderFormValues = z.infer<typeof customProviderSchema>

const DEFAULT_CUSTOM_PROVIDER_VALUES: CustomProviderFormValues = {
  displayName: "",
  baseUrl: "",
  apiKeyHeader: "",
  apiKey: "",
  customHeadersJson: "",
}

const CLOUD_CATALOG_PROVIDERS = [
  "bedrock",
  "azure_openai",
  "azure_ai",
  "vertex_ai",
] as const

type CloudCatalogProvider = (typeof CLOUD_CATALOG_PROVIDERS)[number]

function isCloudCatalogProvider(
  provider: string
): provider is CloudCatalogProvider {
  return (CLOUD_CATALOG_PROVIDERS as readonly string[]).includes(provider)
}

const bedrockCloudModelSchema = z.object({
  provider: z.literal("bedrock"),
  model_name: z.string().trim().min(1, "Model name is required"),
  display_name: z.string().trim().optional(),
  inference_profile_id: z.string().trim().optional(),
  model_id: z.string().trim().optional(),
})

const azureOpenAICloudModelSchema = z.object({
  provider: z.literal("azure_openai"),
  model_name: z.string().trim().min(1, "Model name is required"),
  display_name: z.string().trim().optional(),
  deployment_name: z.string().trim().min(1, "Deployment name is required"),
})

const azureAICloudModelSchema = z.object({
  provider: z.literal("azure_ai"),
  model_name: z.string().trim().min(1, "Model name is required"),
  display_name: z.string().trim().optional(),
  azure_ai_model_name: z
    .string()
    .trim()
    .min(1, "Azure AI model name is required"),
})

const vertexAICloudModelSchema = z.object({
  provider: z.literal("vertex_ai"),
  model_name: z.string().trim().min(1, "Model name is required"),
  display_name: z.string().trim().optional(),
  vertex_model: z.string().trim().min(1, "Vertex model is required"),
})

const cloudCatalogModelSchema = z
  .discriminatedUnion("provider", [
    bedrockCloudModelSchema,
    azureOpenAICloudModelSchema,
    azureAICloudModelSchema,
    vertexAICloudModelSchema,
  ])
  .superRefine((value, ctx) => {
    if (value.provider === "bedrock") {
      const hasProfile = !!value.inference_profile_id
      const hasModelId = !!value.model_id
      if (hasProfile === hasModelId) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Provide exactly one of Inference profile ID or Model ID.",
          path: ["inference_profile_id"],
        })
      }
    }
  })

type CloudCatalogModelFormValues = z.infer<typeof cloudCatalogModelSchema>

function getCatalogMetadataString(
  metadata: Record<string, unknown> | null | undefined,
  key: string
): string {
  if (!metadata) {
    return ""
  }
  const value = metadata[key]
  return typeof value === "string" ? value : ""
}

function buildCloudCatalogDefaults(
  provider: CloudCatalogProvider,
  entry: AgentCatalogRead | null
): CloudCatalogModelFormValues {
  const metadata = entry?.model_metadata ?? null
  const displayName = getCatalogMetadataString(metadata, "display_name")
  const modelName = entry?.model_name ?? ""
  switch (provider) {
    case "bedrock":
      return {
        provider: "bedrock",
        model_name: modelName,
        display_name: displayName,
        inference_profile_id: getCatalogMetadataString(
          metadata,
          "inference_profile_id"
        ),
        model_id: getCatalogMetadataString(metadata, "model_id"),
      }
    case "azure_openai":
      return {
        provider: "azure_openai",
        model_name: modelName,
        display_name: displayName,
        deployment_name: getCatalogMetadataString(metadata, "deployment_name"),
      }
    case "azure_ai":
      return {
        provider: "azure_ai",
        model_name: modelName,
        display_name: displayName,
        azure_ai_model_name: getCatalogMetadataString(
          metadata,
          "azure_ai_model_name"
        ),
      }
    case "vertex_ai":
      return {
        provider: "vertex_ai",
        model_name: modelName,
        display_name: displayName,
        vertex_model: getCatalogMetadataString(metadata, "vertex_model"),
      }
  }
}

function buildCatalogCreatePayload(
  values: CloudCatalogModelFormValues
):
  | BedrockCatalogCreate
  | AzureOpenAICatalogCreate
  | AzureAICatalogCreate
  | VertexAICatalogCreate {
  const displayName = values.display_name?.trim() || null
  switch (values.provider) {
    case "bedrock":
      return {
        model_provider: "bedrock",
        model_name: values.model_name,
        display_name: displayName,
        inference_profile_id: values.inference_profile_id || null,
        model_id: values.model_id || null,
      }
    case "azure_openai":
      return {
        model_provider: "azure_openai",
        model_name: values.model_name,
        display_name: displayName,
        deployment_name: values.deployment_name,
      }
    case "azure_ai":
      return {
        model_provider: "azure_ai",
        model_name: values.model_name,
        display_name: displayName,
        azure_ai_model_name: values.azure_ai_model_name,
      }
    case "vertex_ai":
      return {
        model_provider: "vertex_ai",
        model_name: values.model_name,
        display_name: displayName,
        vertex_model: values.vertex_model,
      }
    default:
      throw new Error(
        `Unsupported cloud provider: ${(values as { provider: string }).provider}`
      )
  }
}

function buildCatalogUpdatePayload(values: CloudCatalogModelFormValues) {
  const displayName = values.display_name?.trim() || null
  switch (values.provider) {
    case "bedrock":
      return {
        model_provider: "bedrock" as const,
        display_name: displayName,
        inference_profile_id: values.inference_profile_id || null,
        model_id: values.model_id || null,
      }
    case "azure_openai":
      return {
        model_provider: "azure_openai" as const,
        display_name: displayName,
        deployment_name: values.deployment_name,
      }
    case "azure_ai":
      return {
        model_provider: "azure_ai" as const,
        display_name: displayName,
        azure_ai_model_name: values.azure_ai_model_name,
      }
    case "vertex_ai":
      return {
        model_provider: "vertex_ai" as const,
        display_name: displayName,
        vertex_model: values.vertex_model,
      }
    default:
      throw new Error(
        `Unsupported cloud provider: ${(values as { provider: string }).provider}`
      )
  }
}

function getCloudProviderLabel(provider: CloudCatalogProvider): string {
  switch (provider) {
    case "bedrock":
      return "AWS Bedrock"
    case "azure_openai":
      return "Azure OpenAI"
    case "azure_ai":
      return "Azure AI"
    case "vertex_ai":
      return "Google Vertex AI"
  }
}

type ModelSelection = {
  source_id: string | null
  model_provider: string
  model_name: string
}

interface ModelCatalogEntry {
  id: string
  source_id: string | null
  source_name: string | null
  source_type: string
  model_provider: string
  model_name: string
  metadata: Record<string, unknown> | null
  base_url: string | null
  enabled: boolean
  readiness_message?: string | null
  runtime_target_configured?: boolean
}

interface BuiltInCatalogEntry extends ModelCatalogEntry {
  enableable?: boolean
  ready?: boolean
  credential_provider?: string | null
  credential_label?: string | null
  credentials_configured?: boolean
  organization_id?: string | null
  metadata_display_name?: string | null
}

interface BuiltInProviderConnection {
  provider: string
  label: string
  source_type: string
  credentials_configured: boolean
  base_url?: string | null
  runtime_target?: string | null
  discovery_status: string
  last_refreshed_at?: string | null
  last_error?: string | null
  discovered_models: BuiltInCatalogEntry[]
}

interface CustomSourceRead {
  id: string
  type: string
  flavor?: string | null
  display_name: string
  base_url?: string | null
  api_key_configured: boolean
  api_key_header?: string | null
  api_version?: string | null
  discovery_status: string
  last_refreshed_at?: string | null
  last_error?: string | null
}

interface CustomSourceCard extends CustomSourceRead {
  models: ModelCatalogEntry[]
  provider: AgentCustomProviderRead
}

function getModelSelectionKey(selection: {
  source_id?: string | null
  model_provider?: string | null
  model_name?: string | null
}): string {
  return `${selection.source_id ?? "platform"}::${selection.model_provider ?? ""}::${selection.model_name ?? ""}`
}

function toModelSelection(
  model: Pick<ModelCatalogEntry, "source_id" | "model_provider" | "model_name">
): ModelSelection {
  return {
    source_id: model.source_id ?? null,
    model_provider: model.model_provider,
    model_name: model.model_name,
  }
}

function normalizeOptional(value: string | null | undefined): string | null {
  if (value == null) {
    return null
  }
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
}

function parseCustomHeaders(
  value: string | null | undefined
): Record<string, string> | null {
  const trimmed = value?.trim()
  if (!trimmed) {
    return null
  }
  return JSON.parse(trimmed) as Record<string, string>
}

function formatDateTime(value?: string | null): string {
  if (!value) {
    return "Never"
  }
  return new Date(value).toLocaleString()
}

function formatStatus(value?: string | null): string {
  if (!value) {
    return "Unknown"
  }
  return value
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ")
}

function getProviderIconId(provider?: string | null): string {
  switch (provider) {
    case "anthropic":
      return "anthropic"
    case "azure_ai":
    case "azure_openai":
      return "microsoft"
    case "bedrock":
      return "amazon-bedrock"
    case "gemini":
    case "vertex_ai":
      return "google"
    case "openai":
      return "openai"
    default:
      return "custom"
  }
}

function normalizeSourceName(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[\s_-]+/g, "")
}

function getCustomSourceTypeLabel(type: string): string {
  switch (type) {
    case "manual_custom":
      return "Manual custom"
    case "openai_compatible_gateway":
      return "OpenAI-compatible"
    default:
      return type.replaceAll("_", " ")
  }
}

function getCustomSourceFlavorLabel(flavor?: string | null): string | null {
  if (!flavor) {
    return null
  }
  switch (flavor) {
    case "generic_openai_compatible":
      return "Generic OpenAI-compatible"
    case "ollama":
      return "Ollama"
    case "vllm":
      return "vLLM"
    case "manual":
      return "Manual"
    default:
      return flavor.replaceAll("_", " ")
  }
}

function getMetadataNumber(metadata: unknown, key: string): number | null {
  if (!metadata || typeof metadata !== "object") {
    return null
  }
  const value = (metadata as Record<string, unknown>)[key]
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

function getMetadataString(metadata: unknown, key: string): string | null {
  if (!metadata || typeof metadata !== "object") {
    return null
  }
  const value = (metadata as Record<string, unknown>)[key]
  return typeof value === "string" && value.trim() ? value : null
}

function formatTokenCount(value: number | null): string {
  if (value == null) {
    return "n/a"
  }
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 1,
    notation: "compact",
  }).format(value)
}

function getModelContextLabel(model: { metadata?: unknown | null }): string {
  return formatTokenCount(getMetadataNumber(model.metadata, "max_input_tokens"))
}

function getModelOutputLabel(model: { metadata?: unknown | null }): string {
  return formatTokenCount(
    getMetadataNumber(model.metadata, "max_output_tokens") ??
      getMetadataNumber(model.metadata, "max_tokens")
  )
}

function getModelModeLabel(model: { metadata?: unknown | null }): string {
  return getMetadataString(model.metadata, "mode") ?? "n/a"
}

function getModelLabel(model: Pick<ModelCatalogEntry, "model_name">): string {
  return model.model_name
}

function getCustomSourceModelTitle(
  model: Pick<ModelCatalogEntry, "model_name" | "metadata">
): string {
  return (
    getMetadataString(model.metadata, "display_name") ??
    getMetadataString(model.metadata, "name") ??
    model.model_name
  )
}

function getModelSourceLabel(
  model: Pick<ModelCatalogEntry, "source_id" | "source_name">
): string {
  return model.source_name ?? (model.source_id ? "Custom" : "Platform")
}

function getCustomSourceIconId(
  source: Pick<CustomSourceRead, "type" | "flavor">
): string {
  switch (source.flavor) {
    case "ollama":
      return "ollama"
    case "vllm":
      return "vllm"
    case "manual":
      return "manual-custom-source"
    default:
      return source.type === "manual_custom" ? "manual-custom-source" : "custom"
  }
}

function inferCustomSourceFlavor(
  provider: Pick<AgentCustomProviderRead, "base_url" | "display_name">
): string | null {
  const candidates = [provider.display_name, provider.base_url ?? ""].map(
    normalizeSourceName
  )

  if (candidates.some((candidate) => candidate.includes("ollama"))) {
    return "ollama"
  }
  if (candidates.some((candidate) => candidate.includes("vllm"))) {
    return "vllm"
  }
  return null
}

function canEnableBuiltInCatalogModel(model: BuiltInCatalogEntry): boolean {
  return (
    model.enabled ||
    (typeof model.enableable === "boolean"
      ? model.enableable
      : typeof model.ready === "boolean"
        ? model.ready
        : true)
  )
}

function ProviderMetaPill({
  active = false,
  children,
}: {
  active?: boolean
  children: ReactNode
}) {
  return (
    <span
      className={cn(
        "inline-flex h-7 items-center rounded-md border px-2.5 text-xs font-medium",
        active
          ? "border-border bg-muted/50 text-foreground"
          : "border-border/60 bg-background text-muted-foreground"
      )}
    >
      {children}
    </span>
  )
}

function getProviderDialogDefaults(
  provider: AgentCustomProviderRead | null
): CustomProviderFormValues {
  if (!provider) {
    return DEFAULT_CUSTOM_PROVIDER_VALUES
  }
  return {
    displayName: provider.display_name,
    baseUrl: provider.base_url ?? "",
    apiKeyHeader: provider.api_key_header ?? "",
    apiKey: "",
    customHeadersJson: "",
  }
}

function buildProviderCreatePayload(
  values: CustomProviderFormValues
): AgentCustomProviderCreate {
  return {
    display_name: values.displayName.trim(),
    base_url: normalizeOptional(values.baseUrl),
    api_key_header: normalizeOptional(values.apiKeyHeader),
    api_key: normalizeOptional(values.apiKey),
    custom_headers: parseCustomHeaders(values.customHeadersJson),
  }
}

function buildProviderUpdatePayload(
  values: CustomProviderFormValues
): AgentCustomProviderUpdate {
  const payload: AgentCustomProviderUpdate = {
    display_name: values.displayName.trim(),
    base_url: normalizeOptional(values.baseUrl),
    api_key_header: normalizeOptional(values.apiKeyHeader),
  }

  const apiKey = normalizeOptional(values.apiKey)
  if (apiKey) {
    payload.api_key = apiKey
  }
  const customHeaders = parseCustomHeaders(values.customHeadersJson)
  if (customHeaders) {
    payload.custom_headers = customHeaders
  }

  return payload
}

async function fetchAllProviders(): Promise<AgentCustomProviderRead[]> {
  const items: AgentCustomProviderRead[] = []
  let cursor: string | undefined

  do {
    const response = await listCustomProviders({
      cursor,
      limit: CURSOR_PAGE_SIZE,
    })
    items.push(...response.items)
    cursor = response.next_cursor ?? undefined
  } while (cursor)

  return items
}

async function fetchAllCatalogEntries(): Promise<AgentCatalogRead[]> {
  const items: AgentCatalogRead[] = []
  let cursor: string | undefined

  do {
    const response = await listCatalog({
      cursor,
      limit: CURSOR_PAGE_SIZE,
    })
    items.push(...response.items)
    cursor = response.next_cursor ?? undefined
  } while (cursor)

  return items
}

async function fetchAllEnabledModels(): Promise<AgentModelAccessRead[]> {
  const items: AgentModelAccessRead[] = []
  let cursor: string | undefined

  do {
    const response = await listEnabledModels({
      cursor,
      limit: CURSOR_PAGE_SIZE,
    })
    items.push(...response.items)
    cursor = response.next_cursor ?? undefined
  } while (cursor)

  return items
}

function CustomProviderDialog({
  provider,
  open,
  onOpenChange,
}: {
  provider: AgentCustomProviderRead | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const queryClient = useQueryClient()
  const form = useForm<CustomProviderFormValues>({
    resolver: zodResolver(customProviderSchema),
    mode: "onBlur",
    defaultValues: getProviderDialogDefaults(provider),
  })

  useEffect(() => {
    form.reset(getProviderDialogDefaults(provider))
  }, [form, provider, open])

  const saveMutation = useMutation({
    mutationFn: async (values: CustomProviderFormValues) => {
      if (provider) {
        return await updateCustomProvider({
          providerId: provider.id,
          requestBody: buildProviderUpdatePayload(values),
        })
      }
      return await createCustomProvider({
        requestBody: buildProviderCreatePayload(values),
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["organization", "agent-providers"],
      })
      onOpenChange(false)
      toast({
        title: provider ? "Custom source updated" : "Custom source created",
        description: provider
          ? "Saved the custom source configuration."
          : "Created the custom source.",
      })
    },
    onError: (error: ApiError) => {
      toast({
        title: provider ? "Update failed" : "Create failed",
        description:
          getApiErrorDetail(error) ?? "Unable to save the custom source.",
        variant: "destructive",
      })
    },
  })

  const validateMutation = useMutation({
    mutationFn: async (values: CustomProviderFormValues) =>
      await validateCustomProviderConnection({
        requestBody: buildProviderCreatePayload(values),
      }),
    onSuccess: (result) => {
      toast({
        title: result.valid ? "Connection looks good" : "Connection failed",
        description: result.valid
          ? "The provider responded successfully."
          : "The provider did not respond successfully.",
        variant: result.valid ? "default" : "destructive",
      })
    },
    onError: (error: ApiError) => {
      toast({
        title: "Connection test failed",
        description:
          getApiErrorDetail(error) ?? "Unable to validate the custom source.",
        variant: "destructive",
      })
    },
  })

  async function handleValidate() {
    const valid = await form.trigger()
    if (!valid) {
      return
    }
    await validateMutation.mutateAsync(form.getValues())
  }

  async function handleSubmit(values: CustomProviderFormValues) {
    await saveMutation.mutateAsync(values)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>
            {provider ? "Edit custom source" : "Add custom source"}
          </DialogTitle>
          <DialogDescription>
            Configure a user-defined OpenAI-compatible endpoint. Discovery reads
            the source&apos;s `/models` endpoint.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(handleSubmit)}
            className="space-y-4"
          >
            <FormField
              control={form.control}
              name="displayName"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Name</FormLabel>
                  <FormControl>
                    <Input {...field} placeholder="Local gateway" />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="grid gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="baseUrl"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Base URL</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        placeholder="http://localhost:11434/v1"
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="apiKeyHeader"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>API key header</FormLabel>
                    <FormControl>
                      <Input {...field} placeholder="Authorization" />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <FormField
              control={form.control}
              name="apiKey"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>API key</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      type="password"
                      placeholder={
                        provider
                          ? "Leave blank to keep the current saved key"
                          : "Optional"
                      }
                    />
                  </FormControl>
                  <FormDescription>
                    Stored encrypted. Leave blank while editing to keep the
                    current value.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="customHeadersJson"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Custom headers JSON</FormLabel>
                  <FormControl>
                    <Textarea
                      {...field}
                      className="min-h-28 font-mono text-xs"
                      placeholder='{"X-API-Key":"value"}'
                    />
                  </FormControl>
                  <FormDescription>
                    Optional JSON object of static headers. Saving new JSON
                    while editing replaces the saved value.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <DialogFooter className="gap-2 sm:gap-0">
              <Button
                type="button"
                variant="outline"
                disabled={saveMutation.isPending || validateMutation.isPending}
                onClick={() => void handleValidate()}
              >
                {validateMutation.isPending ? (
                  <Loader2 className="mr-2 size-4 animate-spin" />
                ) : null}
                Test connection
              </Button>
              <Button
                type="submit"
                disabled={saveMutation.isPending || validateMutation.isPending}
              >
                {saveMutation.isPending ? (
                  <Loader2 className="mr-2 size-4 animate-spin" />
                ) : null}
                {provider ? "Save source" : "Add source"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

function CustomSourceModelRow({
  disabled,
  model,
  onToggle,
}: {
  disabled: boolean
  model: ModelCatalogEntry
  onToggle: (model: ModelCatalogEntry) => Promise<void>
}) {
  const title = getCustomSourceModelTitle(model)
  const showModelName = title !== model.model_name
  const contextLabel = getModelContextLabel(model)
  const outputLabel = getModelOutputLabel(model)
  const modeLabel = getModelModeLabel(model)
  const sourceLabel = getModelSourceLabel(model)
  const detailParts = [
    sourceLabel,
    showModelName ? model.model_name : null,
    modeLabel !== "n/a" ? modeLabel : null,
  ].filter(Boolean)
  const capabilityParts = [
    contextLabel !== "n/a" ? `${contextLabel} ctx` : null,
    outputLabel !== "n/a" ? `${outputLabel} out` : null,
  ].filter(Boolean)

  return (
    <div className="flex flex-col gap-3 rounded-lg border p-4 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
      <div className="min-w-0 space-y-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <p className="truncate text-sm font-medium">{title}</p>
          <Badge variant="outline">{model.model_provider}</Badge>
          {model.base_url ? <Badge variant="outline">Custom URL</Badge> : null}
        </div>
        {detailParts.length ? (
          <p className="truncate text-xs text-muted-foreground">
            {detailParts.join(" · ")}
          </p>
        ) : null}
        {capabilityParts.length ? (
          <p className="truncate text-xs text-muted-foreground">
            {capabilityParts.join(" · ")}
          </p>
        ) : null}
      </div>
      <Button
        disabled={disabled}
        onClick={() => {
          void onToggle(model)
        }}
        size="sm"
        variant={model.enabled ? "secondary" : "outline"}
      >
        {model.enabled ? "Disable" : "Enable"}
      </Button>
    </div>
  )
}

function ProviderAllowlistModelRow({
  disabled,
  model,
  onToggle,
  onEdit,
  onDelete,
}: {
  disabled: boolean
  model: BuiltInCatalogEntry
  onToggle: (model: ModelCatalogEntry) => Promise<void>
  onEdit?: (model: BuiltInCatalogEntry) => void
  onDelete?: (model: BuiltInCatalogEntry) => void
}) {
  const canEnable = canEnableBuiltInCatalogModel(model)
  const statusMessage = model.readiness_message
  const isOrgScoped = model.organization_id != null
  const showRowActions = isOrgScoped && (onEdit || onDelete)
  const displayName = model.metadata_display_name?.trim()

  return (
    <div className="border-b border-border/40 py-3 last:border-b-0">
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_88px_88px_72px_auto] sm:items-center sm:gap-4">
        <div className="min-w-0 space-y-1">
          <div className="flex min-w-0 items-center gap-2">
            <p className="truncate text-sm font-medium">
              {displayName || getModelLabel(model)}
            </p>
            {isOrgScoped ? (
              <Badge className="shrink-0" variant="outline">
                Custom
              </Badge>
            ) : null}
          </div>
          {displayName ? (
            <p className="truncate text-xs text-muted-foreground">
              {model.model_name}
            </p>
          ) : null}
          <p className="text-xs text-muted-foreground sm:hidden">
            {getModelContextLabel(model)} ctx {"·"} {getModelOutputLabel(model)}{" "}
            out {"·"} {getModelModeLabel(model)}
          </p>
          {statusMessage ? (
            <p className="text-xs text-muted-foreground">{statusMessage}</p>
          ) : null}
        </div>
        <p className="hidden text-right text-xs text-muted-foreground sm:block">
          {getModelContextLabel(model)}
        </p>
        <p className="hidden text-right text-xs text-muted-foreground sm:block">
          {getModelOutputLabel(model)}
        </p>
        <p className="hidden text-right text-xs capitalize text-muted-foreground sm:block">
          {getModelModeLabel(model)}
        </p>
        <div className="flex items-center justify-end gap-2">
          {showRowActions ? (
            <>
              {onEdit ? (
                <Button
                  disabled={disabled}
                  onClick={() => onEdit(model)}
                  size="sm"
                  variant="ghost"
                >
                  Edit
                </Button>
              ) : null}
              {onDelete ? (
                <Button
                  disabled={disabled}
                  onClick={() => onDelete(model)}
                  size="sm"
                  variant="ghost"
                >
                  Delete
                </Button>
              ) : null}
            </>
          ) : null}
          <Button
            disabled={disabled || !canEnable}
            onClick={() => {
              void onToggle(model)
            }}
            size="sm"
            variant={model.enabled ? "secondary" : "outline"}
          >
            {model.enabled ? "Disallow" : "Allow"}
          </Button>
        </div>
      </div>
    </div>
  )
}

function ProviderConnectionItem({
  canManageModels,
  disabled,
  enabledCount,
  isExpanded,
  onAddCatalogModel,
  onConfigureProvider,
  onDeleteCatalogModel,
  onDeleteCredentials,
  onDisableAllModels,
  onEditCatalogModel,
  onEnableAllModels,
  onExpandedChange,
  onToggleModel,
  provider,
}: {
  canManageModels: boolean
  disabled: boolean
  enabledCount: number
  isExpanded: boolean
  onAddCatalogModel?: (provider: string) => void
  onConfigureProvider: (provider: string) => void
  onDeleteCatalogModel?: (model: BuiltInCatalogEntry) => void
  onDeleteCredentials: (provider: string, label: string) => Promise<void>
  onDisableAllModels: (provider: string, label: string) => Promise<void>
  onEditCatalogModel?: (model: BuiltInCatalogEntry) => void
  onEnableAllModels: (provider: string, label: string) => Promise<void>
  onExpandedChange: (expanded: boolean) => void
  onToggleModel: (model: ModelCatalogEntry) => Promise<void>
  provider: BuiltInProviderConnection
}) {
  const canExpand = canManageModels
  const [catalogQueryInput, setCatalogQueryInput] = useState("")
  const [allowAllOverride, setAllowAllOverride] = useState<boolean | null>(null)
  const [enabledCountOverride, setEnabledCountOverride] = useState<
    number | null
  >(null)
  const [isWhitelistMode, setIsWhitelistMode] = useState(false)
  const [catalogQuery] = useDebounce(catalogQueryInput.trim(), 250)

  const allProviderModels = provider.discovered_models
  const providerModels = useMemo(() => {
    if (!catalogQuery) {
      return allProviderModels
    }
    const normalizedQuery = catalogQuery.toLowerCase()
    return allProviderModels.filter((model) => {
      const sourceName = getModelSourceLabel(model)
      return (
        model.model_name.toLowerCase().includes(normalizedQuery) ||
        model.model_provider.toLowerCase().includes(normalizedQuery) ||
        sourceName.toLowerCase().includes(normalizedQuery)
      )
    })
  }, [allProviderModels, catalogQuery])

  useEffect(() => {
    setCatalogQueryInput("")
    setAllowAllOverride(null)
    setEnabledCountOverride(null)
    setIsWhitelistMode(false)
  }, [provider.provider])

  const selectableProviderModelCount = allProviderModels.filter((model) =>
    canEnableBuiltInCatalogModel(model)
  ).length
  const enabledSelectableProviderCount = enabledCountOverride ?? enabledCount
  const allSelectableAllowed =
    selectableProviderModelCount > 0 &&
    enabledSelectableProviderCount === selectableProviderModelCount
  const allowAllChecked =
    !isWhitelistMode && (allowAllOverride ?? allSelectableAllowed)
  const showWhitelistControls = isWhitelistMode || !allowAllChecked
  const showModelList = showWhitelistControls
  const subtitle = provider.credentials_configured
    ? provider.base_url || provider.runtime_target
      ? [provider.base_url, provider.runtime_target].filter(Boolean).join(" · ")
      : "Connected"
    : "Not connected"

  return (
    <RbacListItem
      actions={
        <div className="flex flex-wrap items-center justify-end gap-2">
          <ProviderMetaPill active={enabledSelectableProviderCount > 0}>
            {enabledSelectableProviderCount} enabled
          </ProviderMetaPill>
          {provider.credentials_configured && !canManageModels ? (
            <Button
              onClick={() => {
                void onDeleteCredentials(provider.provider, provider.label)
              }}
              size="sm"
              variant="outline"
            >
              Disconnect
            </Button>
          ) : null}
          {onAddCatalogModel ? (
            <Button
              disabled={disabled}
              onClick={() => onAddCatalogModel(provider.provider)}
              size="sm"
              variant="outline"
            >
              Add model
            </Button>
          ) : null}
          <Button
            onClick={() => onConfigureProvider(provider.provider)}
            size="sm"
            variant="outline"
          >
            {provider.credentials_configured ? "Configure" : "Connect"}
          </Button>
        </div>
      }
      badges={null}
      icon={
        <ProviderIcon
          className="size-8 rounded-md"
          providerId={getProviderIconId(provider.provider)}
        />
      }
      isExpanded={isExpanded}
      onExpandedChange={onExpandedChange}
      reserveExpandSpace={canExpand}
      subtitle={subtitle}
      title={provider.label}
    >
      {canExpand ? (
        <div className="space-y-4">
          {provider.credentials_configured ? (
            <div className="flex flex-wrap gap-2">
              <Button
                onClick={() => {
                  void onDeleteCredentials(provider.provider, provider.label)
                }}
                size="sm"
                variant="outline"
              >
                Disconnect
              </Button>
            </div>
          ) : (
            <div className="rounded-lg border border-dashed px-4 py-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="space-y-1">
                  <p className="text-sm font-medium">
                    Connect {provider.label}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    Use the Connect action above to add organization credentials
                    before these models can be enabled for organization use.
                  </p>
                </div>
              </div>
            </div>
          )}

          {provider.last_error ? (
            <div className="rounded-md border border-destructive/40 px-3 py-2 text-sm text-destructive">
              {provider.last_error}
            </div>
          ) : null}

          <div className="rounded-lg border px-4 py-3">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
              <div className="space-y-1">
                <p className="text-sm font-medium">Model access</p>
                <p className="text-xs text-muted-foreground">
                  {allowAllChecked
                    ? `All selectable ${provider.label} models are allowed. Turn this off to manage a whitelist.`
                    : `Use a whitelist to choose which ${provider.label} models can appear in default-model and preset selection.`}
                </p>
              </div>
              <div className="flex items-center gap-3">
                <span
                  className={cn(
                    "text-xs font-medium",
                    allowAllChecked
                      ? "text-foreground"
                      : "text-muted-foreground"
                  )}
                >
                  Allow all
                </span>
                <Switch
                  checked={allowAllChecked}
                  disabled={
                    disabled ||
                    !provider.credentials_configured ||
                    !selectableProviderModelCount
                  }
                  onCheckedChange={(checked) => {
                    if (checked) {
                      setIsWhitelistMode(false)
                      setAllowAllOverride(true)
                      setEnabledCountOverride(selectableProviderModelCount)
                      void onEnableAllModels(
                        provider.provider,
                        provider.label
                      ).catch(() => {
                        setAllowAllOverride(null)
                        setEnabledCountOverride(null)
                      })
                      return
                    }
                    setIsWhitelistMode(true)
                    setAllowAllOverride(false)
                    setEnabledCountOverride(0)
                    void onDisableAllModels(
                      provider.provider,
                      provider.label
                    ).catch(() => {
                      setIsWhitelistMode(false)
                      setAllowAllOverride(null)
                      setEnabledCountOverride(null)
                    })
                  }}
                  size="md"
                />
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <ProviderMetaPill active={enabledSelectableProviderCount > 0}>
                {selectableProviderModelCount
                  ? `${enabledSelectableProviderCount} of ${selectableProviderModelCount} models enabled`
                  : provider.credentials_configured
                    ? "No selectable models available"
                    : "Connect to manage platform models"}
              </ProviderMetaPill>
            </div>
          </div>

          {showModelList ? (
            provider.credentials_configured ? (
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <Input
                    className="focus-visible:ring-0 focus-visible:ring-offset-0"
                    onChange={(event) => {
                      setCatalogQueryInput(event.target.value)
                    }}
                    placeholder={`Search ${provider.label} models`}
                    value={catalogQueryInput}
                  />
                  {catalogQueryInput.trim() ? (
                    <Button
                      onClick={() => {
                        setCatalogQueryInput("")
                      }}
                      size="sm"
                      type="button"
                      variant="ghost"
                    >
                      Clear
                    </Button>
                  ) : null}
                </div>
                {providerModels.length ? (
                  <ScrollArea className="h-96 rounded-lg border">
                    <div className="px-4">
                      <div className="hidden grid-cols-[minmax(0,1fr)_88px_88px_72px_auto] gap-4 border-b border-border/40 py-3 text-[11px] font-medium uppercase tracking-wide text-muted-foreground sm:grid">
                        <span>Model</span>
                        <span className="text-right">Context</span>
                        <span className="text-right">Output</span>
                        <span className="text-right">Mode</span>
                        <span className="text-right">Access</span>
                      </div>
                      {providerModels.map((model) => (
                        <ProviderAllowlistModelRow
                          disabled={disabled}
                          key={getModelSelectionKey(toModelSelection(model))}
                          model={model}
                          onDelete={onDeleteCatalogModel}
                          onEdit={onEditCatalogModel}
                          onToggle={async (nextModel) => {
                            const nextCountDelta = nextModel.enabled ? -1 : 1
                            setAllowAllOverride(null)
                            setEnabledCountOverride((current) =>
                              Math.min(
                                selectableProviderModelCount ||
                                  Number.POSITIVE_INFINITY,
                                Math.max(
                                  0,
                                  (current ?? enabledCount) + nextCountDelta
                                )
                              )
                            )
                            try {
                              await onToggleModel(nextModel)
                            } catch {
                              setEnabledCountOverride(null)
                            }
                          }}
                        />
                      ))}
                    </div>
                  </ScrollArea>
                ) : (
                  <div className="rounded-md border border-dashed px-3 py-4 text-sm text-muted-foreground">
                    {catalogQueryInput.trim()
                      ? `No ${provider.label} models matched this search.`
                      : "No selectable models are currently available for this provider."}
                  </div>
                )}
              </div>
            ) : (
              <div className="rounded-md border border-dashed px-3 py-4 text-sm text-muted-foreground">
                Connect this provider, then allow the platform models your
                organization should be able to choose.
              </div>
            )
          ) : (
            <div className="rounded-md border border-dashed px-3 py-4 text-sm text-muted-foreground">
              Every selectable {provider.label} model is allowed. Turn off Allow
              all to trim this down to a whitelist.
            </div>
          )}
        </div>
      ) : null}
    </RbacListItem>
  )
}

interface CloudCatalogModelDialogProps {
  provider: CloudCatalogProvider | null
  entry: AgentCatalogRead | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

/**
 * Create or edit an org-scoped cloud catalog entry for Bedrock, Azure OpenAI,
 * Azure AI, or Vertex AI.
 */
function CloudCatalogModelDialog({
  provider,
  entry,
  open,
  onOpenChange,
}: CloudCatalogModelDialogProps) {
  const queryClient = useQueryClient()
  const activeProvider = provider ?? "bedrock"
  const form = useForm<CloudCatalogModelFormValues>({
    resolver: zodResolver(cloudCatalogModelSchema),
    mode: "onBlur",
    defaultValues: buildCloudCatalogDefaults(activeProvider, entry),
  })

  useEffect(() => {
    if (!open || !provider) {
      return
    }
    form.reset(buildCloudCatalogDefaults(provider, entry))
  }, [form, provider, entry, open])

  const saveMutation = useMutation({
    mutationFn: async (values: CloudCatalogModelFormValues) => {
      if (entry) {
        return await updateCatalogEntry({
          catalogId: entry.id,
          requestBody: buildCatalogUpdatePayload(values),
        })
      }
      return await createCatalogEntry({
        requestBody: buildCatalogCreatePayload(values),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["organization", "agent-catalog"],
      })
      onOpenChange(false)
      toast({
        title: entry ? "Model updated" : "Model added",
        description: entry
          ? "Saved the catalog entry."
          : "Added the catalog entry.",
      })
    },
    onError: (error: ApiError) => {
      toast({
        title: entry ? "Update failed" : "Add failed",
        description:
          getApiErrorDetail(error) ?? "Unable to save the catalog entry.",
        variant: "destructive",
      })
    },
  })

  async function handleSubmit(values: CloudCatalogModelFormValues) {
    await saveMutation.mutateAsync(values)
  }

  if (!provider) {
    return null
  }

  const label = getCloudProviderLabel(provider)
  const isEdit = entry !== null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>
            {isEdit ? `Edit ${label} model` : `Add ${label} model`}
          </DialogTitle>
          <DialogDescription>
            {provider === "bedrock"
              ? "Reference a Bedrock inference profile or a direct model ID."
              : provider === "azure_openai"
                ? "Map a catalog entry to an Azure OpenAI deployment."
                : provider === "azure_ai"
                  ? "Map a catalog entry to an Azure AI model name."
                  : "Map a catalog entry to a Vertex AI model."}
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(handleSubmit)}
            className="space-y-4"
          >
            <FormField
              control={form.control}
              name="model_name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Model name</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      disabled={isEdit}
                      placeholder="e.g. claude-sonnet-4"
                    />
                  </FormControl>
                  <FormDescription>
                    Catalog identifier exposed to presets and default-model
                    selection. Cannot be changed after creation.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="display_name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Display name</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      value={field.value ?? ""}
                      placeholder="Optional friendly label"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            {provider === "bedrock" ? (
              <>
                <FormField
                  control={form.control}
                  name="inference_profile_id"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Inference profile ID</FormLabel>
                      <FormControl>
                        <Input
                          {...field}
                          value={field.value ?? ""}
                          placeholder="us.anthropic.claude-sonnet-4-20250514-v1:0"
                        />
                      </FormControl>
                      <FormDescription>
                        Required for newer models. Provide either this or a
                        direct model ID.
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="model_id"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Model ID</FormLabel>
                      <FormControl>
                        <Input
                          {...field}
                          value={field.value ?? ""}
                          placeholder="anthropic.claude-3-haiku-20240307-v1:0"
                        />
                      </FormControl>
                      <FormDescription>
                        Direct model ID for older on-demand models.
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </>
            ) : null}

            {provider === "azure_openai" ? (
              <FormField
                control={form.control}
                name="deployment_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Deployment name</FormLabel>
                    <FormControl>
                      <Input {...field} placeholder="gpt-4o" />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            ) : null}

            {provider === "azure_ai" ? (
              <FormField
                control={form.control}
                name="azure_ai_model_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Azure AI model name</FormLabel>
                    <FormControl>
                      <Input {...field} placeholder="claude-sonnet-4-5" />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            ) : null}

            {provider === "vertex_ai" ? (
              <FormField
                control={form.control}
                name="vertex_model"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Vertex model</FormLabel>
                    <FormControl>
                      <Input {...field} placeholder="gemini-3-flash" />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            ) : null}

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={saveMutation.isPending}
                onClick={() => onOpenChange(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={saveMutation.isPending}>
                {saveMutation.isPending ? (
                  <Loader2 className="mr-2 size-4 animate-spin" />
                ) : null}
                {isEdit ? "Save model" : "Add model"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

/**
 * Organization agent settings using the `feat/llm-provider-v2` layout, backed
 * by the current backend and OpenAPI surface.
 */
export function OrgSettingsAgentForm() {
  const queryClient = useQueryClient()
  const { hasEntitlement } = useEntitlements()
  const agentAddonsEnabled = hasEntitlement("agent_addons")
  const [credentialsProvider, setCredentialsProvider] = useState<string | null>(
    null
  )
  const [editingProvider, setEditingProvider] =
    useState<AgentCustomProviderRead | null>(null)
  const [deletingProvider, setDeletingProvider] =
    useState<AgentCustomProviderRead | null>(null)
  const [expandedProvider, setExpandedProvider] = useState<string | null>(null)
  const [customProviderDialogOpen, setCustomProviderDialogOpen] =
    useState(false)
  const [cloudModelDialog, setCloudModelDialog] = useState<{
    provider: CloudCatalogProvider
    entry: AgentCatalogRead | null
  } | null>(null)
  const [deletingCatalogEntry, setDeletingCatalogEntry] =
    useState<AgentCatalogRead | null>(null)

  const { providerConfigs, providerConfigsLoading, providerConfigsError } =
    useProviderCredentialConfigs()
  const {
    providersStatus,
    isLoading: providersStatusLoading,
    error: providersStatusError,
  } = useModelProvidersStatus()
  const {
    defaultModel,
    defaultModelLoading,
    defaultModelError,
    updateDefaultModel,
    isUpdating,
  } = useAgentDefaultModel()

  const {
    data: customProviders,
    isLoading: customProvidersLoading,
    error: customProvidersError,
  } = useQuery<AgentCustomProviderRead[], ApiError>({
    queryKey: ["organization", "agent-providers"],
    queryFn: fetchAllProviders,
    retry: retryHandler,
  })

  const {
    data: catalogEntries,
    isLoading: catalogEntriesLoading,
    error: catalogEntriesError,
  } = useQuery<AgentCatalogRead[], ApiError>({
    queryKey: ["organization", "agent-catalog"],
    queryFn: fetchAllCatalogEntries,
    retry: retryHandler,
  })

  const {
    data: enabledAccessRows,
    isLoading: enabledAccessRowsLoading,
    error: enabledAccessRowsError,
  } = useQuery<AgentModelAccessRead[], ApiError>({
    queryKey: ["organization", "agent-model-access"],
    queryFn: fetchAllEnabledModels,
    retry: retryHandler,
  })

  const providerConfigsByProvider = useMemo(
    () =>
      new Map(
        (providerConfigs ?? []).map((providerConfig) => [
          providerConfig.provider,
          providerConfig,
        ])
      ),
    [providerConfigs]
  )

  const orgEnabledAccessRows = useMemo(
    () => (enabledAccessRows ?? []).filter((row) => row.workspace_id === null),
    [enabledAccessRows]
  )
  const enabledCatalogIdToAccess = useMemo(
    () => new Map(orgEnabledAccessRows.map((row) => [row.catalog_id, row])),
    [orgEnabledAccessRows]
  )

  const customProvidersById = useMemo(
    () =>
      new Map(
        (customProviders ?? []).map((provider) => [provider.id, provider])
      ),
    [customProviders]
  )

  const orgEnabledCatalogEntries = useMemo(
    () =>
      (catalogEntries ?? []).filter((entry) =>
        enabledCatalogIdToAccess.has(entry.id)
      ),
    [catalogEntries, enabledCatalogIdToAccess]
  )

  const defaultModelOptions = useMemo(
    () =>
      [...orgEnabledCatalogEntries]
        .sort((left, right) => left.model_name.localeCompare(right.model_name))
        .map((entry) => {
          const customProvider = entry.custom_provider_id
            ? customProvidersById.get(entry.custom_provider_id)
            : null
          const sourceLabel =
            customProvider?.display_name ??
            providerConfigsByProvider.get(entry.model_provider)?.label ??
            entry.model_provider
          return {
            model_name: entry.model_name,
            model_provider: entry.model_provider,
            source_label: sourceLabel,
          }
        }),
    [orgEnabledCatalogEntries, customProvidersById, providerConfigsByProvider]
  )
  const currentDefaultModelOption =
    defaultModelOptions.find((model) => model.model_name === defaultModel) ??
    null
  const builtInCatalogEntries = useMemo(
    () =>
      (catalogEntries ?? []).filter(
        (entry) => entry.custom_provider_id === null
      ),
    [catalogEntries]
  )
  const builtInProviders = useMemo(() => {
    const entriesByProvider = new Map<string, AgentCatalogRead[]>()

    for (const entry of builtInCatalogEntries) {
      const current = entriesByProvider.get(entry.model_provider)
      if (current) {
        current.push(entry)
      } else {
        entriesByProvider.set(entry.model_provider, [entry])
      }
    }

    return [...(providerConfigs ?? [])]
      .filter(
        // The custom-model-provider slug has its own dedicated "Custom
        // sources" section below; excluding it from the built-in provider
        // list prevents a duplicate card.
        (providerConfig) => providerConfig.provider !== "custom-model-provider"
      )
      .sort((left, right) => left.label.localeCompare(right.label))
      .map((providerConfig): BuiltInProviderConnection => {
        const credentialsConfigured =
          providersStatus?.[providerConfig.provider] ?? false
        const discoveredModels = [
          ...(entriesByProvider.get(providerConfig.provider) ?? []),
        ]
          .sort((left, right) =>
            left.model_name.localeCompare(right.model_name)
          )
          .map(
            (entry): BuiltInCatalogEntry => ({
              id: entry.id,
              source_id: null,
              source_name:
                entry.organization_id === null ? "Platform" : "Organization",
              source_type:
                entry.organization_id === null ? "platform" : "organization",
              model_provider: entry.model_provider,
              model_name: entry.model_name,
              metadata: entry.model_metadata,
              base_url: null,
              enabled: enabledCatalogIdToAccess.has(entry.id),
              enableable: credentialsConfigured,
              ready: credentialsConfigured,
              credentials_configured: credentialsConfigured,
              credential_provider: providerConfig.provider,
              credential_label: providerConfig.label,
              organization_id: entry.organization_id,
              metadata_display_name: getCatalogMetadataString(
                entry.model_metadata,
                "display_name"
              ),
              readiness_message: credentialsConfigured
                ? null
                : "Connect this provider before allowing its platform models.",
            })
          )

        return {
          provider: providerConfig.provider,
          label: providerConfig.label,
          source_type: "platform",
          credentials_configured: credentialsConfigured,
          base_url: null,
          runtime_target: null,
          discovery_status: discoveredModels.length ? "loaded" : "unknown",
          last_refreshed_at: null,
          last_error: null,
          discovered_models: discoveredModels,
        }
      })
  }, [
    enabledCatalogIdToAccess,
    builtInCatalogEntries,
    providerConfigs,
    providersStatus,
  ])
  const builtInProvidersById = useMemo(
    () =>
      new Map(
        builtInProviders.map((provider) => [provider.provider, provider])
      ),
    [builtInProviders]
  )
  const customSourceCards = useMemo(() => {
    const catalogByProviderId = new Map<string, AgentCatalogRead[]>()

    for (const entry of catalogEntries ?? []) {
      if (!entry.custom_provider_id) {
        continue
      }
      const current = catalogByProviderId.get(entry.custom_provider_id)
      if (current) {
        current.push(entry)
      } else {
        catalogByProviderId.set(entry.custom_provider_id, [entry])
      }
    }

    return [...(customProviders ?? [])]
      .sort((left, right) =>
        left.display_name.localeCompare(right.display_name)
      )
      .map((provider): CustomSourceCard => {
        const sourceFlavor = inferCustomSourceFlavor(provider)
        const providerEntries = [
          ...(catalogByProviderId.get(provider.id) ?? []),
        ]
          .sort((left, right) =>
            left.model_name.localeCompare(right.model_name)
          )
          .map(
            (entry): ModelCatalogEntry => ({
              id: entry.id,
              source_id: provider.id,
              source_name: provider.display_name,
              source_type: "openai_compatible_gateway",
              model_provider: entry.model_provider,
              model_name: entry.model_name,
              metadata: entry.model_metadata,
              base_url: provider.base_url,
              enabled: enabledCatalogIdToAccess.has(entry.id),
            })
          )

        return {
          id: provider.id,
          type: "openai_compatible_gateway",
          flavor: sourceFlavor,
          display_name: provider.display_name,
          base_url: provider.base_url,
          api_key_configured: false,
          api_key_header: provider.api_key_header,
          discovery_status: providerEntries.length ? "loaded" : "unknown",
          last_refreshed_at: provider.last_refreshed_at,
          last_error: null,
          models: providerEntries,
          provider,
        }
      })
  }, [catalogEntries, customProviders, enabledCatalogIdToAccess])

  const pageLoading =
    !providerConfigs &&
    !customProviders &&
    !catalogEntries &&
    !enabledAccessRows &&
    (providerConfigsLoading ||
      providersStatusLoading ||
      customProvidersLoading ||
      catalogEntriesLoading ||
      enabledAccessRowsLoading ||
      defaultModelLoading)

  function invalidateOrganizationAgentQueries() {
    void queryClient.invalidateQueries({
      queryKey: ["organization", "agent-providers"],
    })
    void queryClient.invalidateQueries({
      queryKey: ["organization", "agent-catalog"],
    })
    void queryClient.invalidateQueries({
      queryKey: ["organization", "agent-model-access"],
    })
  }

  function invalidateBuiltInAgentQueries() {
    void queryClient.invalidateQueries({
      queryKey: ["agent-providers-status"],
    })
    void queryClient.invalidateQueries({
      queryKey: ["agent-default-model"],
    })
  }

  const credentialDeleteMutation = useMutation({
    mutationFn: async (provider: string) =>
      await agentDeleteProviderCredentials({ provider }),
    onSuccess: () => {
      invalidateBuiltInAgentQueries()
    },
  })
  const refreshProviderMutation = useMutation({
    mutationFn: async (providerId: string) =>
      await refreshCustomProviderCatalog({ providerId }),
    onSuccess: () => {
      invalidateOrganizationAgentQueries()
    },
  })
  const deleteCustomProviderMutation = useMutation({
    mutationFn: async (providerId: string) =>
      await deleteCustomProvider({ providerId }),
    onSuccess: () => {
      invalidateOrganizationAgentQueries()
    },
  })
  const deleteCatalogEntryMutation = useMutation({
    mutationFn: async (catalogId: string) =>
      await deleteCatalogEntry({ catalogId }),
    onSuccess: () => {
      invalidateOrganizationAgentQueries()
    },
  })
  const toggleCatalogAccessMutation = useMutation({
    mutationFn: async (model: ModelCatalogEntry) => {
      const existingAccess = enabledCatalogIdToAccess.get(model.id)
      if (existingAccess) {
        await disableModel({ accessId: existingAccess.id })
        return
      }
      await enableModel({
        requestBody: {
          catalog_id: model.id,
        },
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["organization", "agent-model-access"],
      })
    },
  })
  const bulkCatalogAccessMutation = useMutation({
    mutationFn: async ({
      accessIdsToDisable,
      catalogIdsToEnable,
    }: {
      accessIdsToDisable: string[]
      catalogIdsToEnable: string[]
    }) => {
      if (catalogIdsToEnable.length) {
        await Promise.all(
          catalogIdsToEnable.map(
            async (catalogId) =>
              await enableModel({
                requestBody: {
                  catalog_id: catalogId,
                },
              })
          )
        )
      }
      if (accessIdsToDisable.length) {
        await Promise.all(
          accessIdsToDisable.map(
            async (accessId) => await disableModel({ accessId })
          )
        )
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["organization", "agent-model-access"],
      })
    },
  })

  async function handleDeleteBuiltInCredentials(
    provider: string,
    label: string
  ) {
    if (
      !window.confirm(
        `Delete the saved ${label} credentials for this organization?`
      )
    ) {
      return
    }

    try {
      await credentialDeleteMutation.mutateAsync(provider)
      toast({
        title: "Credentials deleted",
        description: `${label} credentials were removed.`,
      })
    } catch (error) {
      toast({
        title: `Failed to delete ${label} credentials`,
        description:
          getApiErrorDetail(error) ??
          "Unable to delete the provider credentials.",
        variant: "destructive",
      })
    }
  }

  async function handleDefaultModelChange(nextModel: string) {
    try {
      await updateDefaultModel(nextModel)
      toast({
        title: "Default model updated",
      })
    } catch (error) {
      toast({
        title: "Update failed",
        description:
          getApiErrorDetail(error) ?? "Unable to update the default model.",
        variant: "destructive",
      })
    }
  }

  async function handleModelToggle(model: ModelCatalogEntry) {
    try {
      await toggleCatalogAccessMutation.mutateAsync(model)
    } catch (error) {
      toast({
        title: `Failed to ${model.enabled ? "disable" : "enable"} ${getModelLabel(model)}`,
        description:
          getApiErrorDetail(error) ?? "Unable to update model access.",
        variant: "destructive",
      })
      throw error
    }
  }

  async function handleEnableAllProviderModels(
    providerId: string,
    label: string
  ) {
    const provider = builtInProvidersById.get(providerId)
    if (!provider) {
      return
    }

    const catalogIdsToEnable = provider.discovered_models
      .filter((model) => canEnableBuiltInCatalogModel(model) && !model.enabled)
      .map((model) => model.id)

    try {
      await bulkCatalogAccessMutation.mutateAsync({
        accessIdsToDisable: [],
        catalogIdsToEnable,
      })
      toast({
        title: "Provider access updated",
        description: catalogIdsToEnable.length
          ? `Every selectable ${label} model is now allowed.`
          : `All selectable ${label} models were already allowed.`,
      })
    } catch (error) {
      toast({
        title: `Failed to update ${label} model access`,
        description:
          getApiErrorDetail(error) ?? "Unable to update model access.",
        variant: "destructive",
      })
      throw error
    }
  }

  async function handleDisableAllProviderModels(
    providerId: string,
    label: string
  ) {
    const provider = builtInProvidersById.get(providerId)
    if (!provider) {
      return
    }

    const accessIdsToDisable = provider.discovered_models.flatMap((model) => {
      const access = enabledCatalogIdToAccess.get(model.id)
      return access ? [access.id] : []
    })

    try {
      await bulkCatalogAccessMutation.mutateAsync({
        accessIdsToDisable,
        catalogIdsToEnable: [],
      })
      toast({
        title: "Provider access updated",
        description: accessIdsToDisable.length
          ? `All ${label} models were removed from the allowlist.`
          : `No ${label} models were currently allowed.`,
      })
    } catch (error) {
      toast({
        title: `Failed to update ${label} model access`,
        description:
          getApiErrorDetail(error) ?? "Unable to update model access.",
        variant: "destructive",
      })
      throw error
    }
  }

  async function handleRefreshCustomProvider(source: CustomSourceCard) {
    try {
      await refreshProviderMutation.mutateAsync(source.id)
      toast({
        title: "Custom source refreshed",
        description: `Updated ${source.display_name}.`,
      })
    } catch (error) {
      toast({
        title: `Failed to refresh ${source.display_name}`,
        description:
          getApiErrorDetail(error) ??
          "Unable to refresh the custom provider catalog.",
        variant: "destructive",
      })
    }
  }

  async function handleDeleteCustomProvider(provider: AgentCustomProviderRead) {
    try {
      await deleteCustomProviderMutation.mutateAsync(provider.id)
      toast({
        title: "Custom source deleted",
        description: `${provider.display_name} was removed.`,
      })
    } catch (error) {
      toast({
        title: `Failed to delete ${provider.display_name}`,
        description:
          getApiErrorDetail(error) ?? "Unable to delete the custom provider.",
        variant: "destructive",
      })
    }
  }

  async function handleProviderCredentialsSaved() {
    invalidateBuiltInAgentQueries()

    if (!credentialsProvider || selectedCredentialsConfigured) {
      return
    }

    const provider = builtInProvidersById.get(credentialsProvider)
    if (!provider) {
      return
    }

    const catalogIdsToEnable = provider.discovered_models
      .filter((model) => !model.enabled)
      .map((model) => model.id)

    if (!catalogIdsToEnable.length) {
      return
    }

    try {
      await bulkCatalogAccessMutation.mutateAsync({
        accessIdsToDisable: [],
        catalogIdsToEnable,
      })
      toast({
        title: "Provider connected",
        description:
          catalogIdsToEnable.length === 1
            ? `Enabled the ${provider.label} model by default.`
            : `Enabled all ${provider.label} models by default.`,
      })
    } catch (error) {
      toast({
        title: "Credentials saved, but model enablement failed",
        description:
          getApiErrorDetail(error) ??
          "The provider credentials were saved, but its models were not enabled automatically.",
        variant: "destructive",
      })
    }
  }

  function handleOpenAddCatalogModel(providerSlug: string) {
    if (!isCloudCatalogProvider(providerSlug)) {
      return
    }
    setCloudModelDialog({ provider: providerSlug, entry: null })
  }

  function handleOpenEditCatalogModel(model: BuiltInCatalogEntry) {
    if (!isCloudCatalogProvider(model.model_provider)) {
      return
    }
    const source = (catalogEntries ?? []).find((entry) => entry.id === model.id)
    if (!source) {
      return
    }
    setCloudModelDialog({
      provider: model.model_provider,
      entry: source,
    })
  }

  function handleOpenDeleteCatalogModel(model: BuiltInCatalogEntry) {
    const source = (catalogEntries ?? []).find((entry) => entry.id === model.id)
    if (!source) {
      return
    }
    setDeletingCatalogEntry(source)
  }

  async function handleConfirmDeleteCatalogEntry(entry: AgentCatalogRead) {
    try {
      await deleteCatalogEntryMutation.mutateAsync(entry.id)
      toast({
        title: "Model deleted",
        description: `Removed ${entry.model_name} from the catalog.`,
      })
    } catch (error) {
      toast({
        title: "Delete failed",
        description:
          getApiErrorDetail(error) ?? "Unable to delete the catalog entry.",
        variant: "destructive",
      })
    } finally {
      setDeletingCatalogEntry(null)
    }
  }

  if (pageLoading) {
    return <CenteredSpinner />
  }

  const pageError =
    providerConfigsError ??
    providersStatusError ??
    customProvidersError ??
    catalogEntriesError ??
    enabledAccessRowsError ??
    defaultModelError
  const providerSectionLoading =
    providerConfigsLoading ||
    providersStatusLoading ||
    catalogEntriesLoading ||
    enabledAccessRowsLoading
  const customSourcesSectionLoading =
    customProvidersLoading || catalogEntriesLoading || enabledAccessRowsLoading
  const defaultModelSectionLoading =
    defaultModelLoading ||
    catalogEntriesLoading ||
    enabledAccessRowsLoading ||
    providersStatusLoading ||
    providerConfigsLoading
  const isSelectionUpdating =
    toggleCatalogAccessMutation.isPending ||
    bulkCatalogAccessMutation.isPending ||
    isUpdating
  const selectedCredentialsConfigured = credentialsProvider
    ? (providersStatus?.[credentialsProvider] ?? false)
    : false

  return (
    <div className="space-y-12">
      {pageError ? (
        <AlertNotification
          level="error"
          message={
            getApiErrorDetail(pageError) ??
            "Unable to load model provider settings."
          }
        />
      ) : null}

      {agentAddonsEnabled ? (
        <section className="space-y-4">
          <div className="space-y-1">
            <h3 className="text-lg font-semibold tracking-tight">
              Default model
            </h3>
            <p className="text-sm text-muted-foreground">
              Choose the organization-wide default from the models enabled for
              this organization.
            </p>
          </div>

          {defaultModelSectionLoading ? (
            <CenteredSpinner />
          ) : !defaultModelOptions.length ? (
            <p className="text-sm text-muted-foreground">
              Enable at least one model below to choose a default.
            </p>
          ) : (
            <Select
              disabled={isSelectionUpdating}
              onValueChange={(modelName) => {
                if (modelName === defaultModel) {
                  return
                }
                void handleDefaultModelChange(modelName)
              }}
              value={currentDefaultModelOption?.model_name ?? ""}
            >
              <SelectTrigger className="h-12 px-4 [&>svg]:shrink-0">
                {currentDefaultModelOption ? (
                  <div className="flex min-w-0 items-center gap-3 text-left">
                    <ProviderIcon
                      className="size-5 rounded-sm p-0.5"
                      providerId={getProviderIconId(
                        currentDefaultModelOption.model_provider
                      )}
                    />
                    <div className="min-w-0 space-y-0.5">
                      <span className="block truncate text-sm font-medium text-foreground">
                        {currentDefaultModelOption.model_name}
                      </span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {currentDefaultModelOption.source_label}
                      </span>
                    </div>
                  </div>
                ) : (
                  <SelectValue placeholder="Choose a default model" />
                )}
              </SelectTrigger>
              <SelectContent>
                {defaultModelOptions.map((model) => {
                  const isSelected = model.model_name === defaultModel

                  return (
                    <SelectItem key={model.model_name} value={model.model_name}>
                      <div className="flex min-w-0 items-start gap-3 py-1">
                        <ProviderIcon
                          className="mt-0.5 size-5 rounded-sm p-0.5"
                          providerId={getProviderIconId(model.model_provider)}
                        />
                        <div className="min-w-0 space-y-1">
                          <div className="flex min-w-0 items-center gap-2">
                            <span className="truncate text-sm font-medium">
                              {model.model_name}
                            </span>
                            {isSelected ? (
                              <span className="shrink-0 text-xs text-muted-foreground">
                                Current default
                              </span>
                            ) : null}
                          </div>
                          <p className="truncate text-xs text-muted-foreground">
                            {model.source_label}
                          </p>
                        </div>
                      </div>
                    </SelectItem>
                  )
                })}
              </SelectContent>
            </Select>
          )}

          {isSelectionUpdating ? (
            <p className="text-xs text-muted-foreground">Saving changes…</p>
          ) : null}
        </section>
      ) : null}

      <section className="space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-1">
            <h3 className="text-lg font-semibold tracking-tight">
              Provider connections
            </h3>
            <p className="text-sm text-muted-foreground">
              Connect a provider, open it, and allow the organization-level
              models your workspaces can draw from.
            </p>
          </div>
        </div>
        <div className="space-y-1">
          <p className="text-xs text-muted-foreground">
            {agentAddonsEnabled
              ? "The platform catalog stays shared, while each workspace can still restrict itself to a smaller subset of the organization-enabled catalog."
              : "Connect providers here. Upgrade to manage which organization-level models are enabled."}
          </p>
        </div>
        <div className="space-y-4">
          {providerSectionLoading ? (
            <CenteredSpinner />
          ) : builtInProviders.length ? (
            <RbacListContainer>
              {builtInProviders.map((provider) => {
                const enabledCount = provider.discovered_models.filter(
                  (model) => model.enabled
                ).length

                const supportsCatalogAuthoring =
                  agentAddonsEnabled &&
                  isCloudCatalogProvider(provider.provider)
                return (
                  <ProviderConnectionItem
                    canManageModels={agentAddonsEnabled}
                    disabled={
                      isSelectionUpdating || credentialDeleteMutation.isPending
                    }
                    enabledCount={enabledCount}
                    isExpanded={
                      agentAddonsEnabled &&
                      expandedProvider === provider.provider
                    }
                    key={provider.provider}
                    onAddCatalogModel={
                      supportsCatalogAuthoring
                        ? handleOpenAddCatalogModel
                        : undefined
                    }
                    onConfigureProvider={setCredentialsProvider}
                    onDeleteCatalogModel={
                      supportsCatalogAuthoring
                        ? handleOpenDeleteCatalogModel
                        : undefined
                    }
                    onDeleteCredentials={handleDeleteBuiltInCredentials}
                    onDisableAllModels={handleDisableAllProviderModels}
                    onEditCatalogModel={
                      supportsCatalogAuthoring
                        ? handleOpenEditCatalogModel
                        : undefined
                    }
                    onEnableAllModels={handleEnableAllProviderModels}
                    onExpandedChange={(expanded) => {
                      setExpandedProvider(
                        agentAddonsEnabled && expanded
                          ? provider.provider
                          : null
                      )
                    }}
                    onToggleModel={handleModelToggle}
                    provider={provider}
                  />
                )
              })}
            </RbacListContainer>
          ) : (
            <div className="rounded-md border border-dashed px-3 py-4 text-sm text-muted-foreground">
              No platform providers are available.
            </div>
          )}
        </div>
      </section>

      <section className="space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-1">
            <h3 className="text-lg font-semibold tracking-tight">
              Custom sources
            </h3>
            <p className="text-sm text-muted-foreground">
              Add custom sources like Ollama, vLLM, or other OpenAI-compatible
              gateways.
            </p>
          </div>
          <Button
            onClick={() => {
              setEditingProvider(null)
              setCustomProviderDialogOpen(true)
            }}
            variant="outline"
          >
            Add custom source
          </Button>
        </div>

        {customSourcesSectionLoading ? (
          <CenteredSpinner />
        ) : customSourceCards.length ? (
          <div className="space-y-4">
            {customSourceCards.map((source) => (
              <Card key={source.id}>
                <CardHeader className="space-y-4">
                  <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                    <div className="flex items-start gap-3">
                      <ProviderIcon
                        className="size-8 rounded-md"
                        providerId={getCustomSourceIconId(source)}
                      />
                      <div className="space-y-2">
                        <CardTitle>{source.display_name}</CardTitle>
                        <CardDescription>
                          {getCustomSourceTypeLabel(source.type)}
                          {getCustomSourceFlavorLabel(source.flavor)
                            ? ` · ${getCustomSourceFlavorLabel(source.flavor)}`
                            : ""}
                          {` · ${formatStatus(source.discovery_status)}`}
                        </CardDescription>
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        disabled={
                          refreshProviderMutation.isPending ||
                          deleteCustomProviderMutation.isPending
                        }
                        onClick={() => {
                          setEditingProvider(source.provider)
                          setCustomProviderDialogOpen(true)
                        }}
                        size="sm"
                        variant="outline"
                      >
                        Edit
                      </Button>
                      <Button
                        disabled={
                          refreshProviderMutation.isPending ||
                          deleteCustomProviderMutation.isPending
                        }
                        onClick={() => {
                          void handleRefreshCustomProvider(source)
                        }}
                        size="sm"
                        variant="outline"
                      >
                        Refresh
                      </Button>
                      <Button
                        disabled={
                          refreshProviderMutation.isPending ||
                          deleteCustomProviderMutation.isPending
                        }
                        onClick={() => setDeletingProvider(source.provider)}
                        size="sm"
                        variant="outline"
                      >
                        Delete
                      </Button>
                    </div>
                  </div>
                  <div className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
                    <p>
                      Last refreshed: {formatDateTime(source.last_refreshed_at)}
                    </p>
                    {source.base_url ? (
                      <p>
                        Base URL:{" "}
                        <span className="font-mono">{source.base_url}</span>
                      </p>
                    ) : null}
                  </div>
                </CardHeader>
                <CardContent className="space-y-4">
                  {source.last_error ? (
                    <div className="rounded-lg border border-destructive/40 p-4 text-sm text-destructive">
                      {source.last_error}
                    </div>
                  ) : null}

                  {!source.base_url ? (
                    <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
                      Set a base URL before refreshing this custom endpoint.
                    </div>
                  ) : null}

                  {source.models.length ? (
                    <>
                      {!agentAddonsEnabled ? (
                        <div className="rounded-lg border border-dashed p-3 text-xs text-muted-foreground">
                          Upgrade to enable or disable specific source-backed
                          models for presets and defaults.
                        </div>
                      ) : null}
                      {source.models.map((model) => (
                        <CustomSourceModelRow
                          disabled={!agentAddonsEnabled || isSelectionUpdating}
                          key={getModelSelectionKey(toModelSelection(model))}
                          model={model}
                          onToggle={handleModelToggle}
                        />
                      ))}
                    </>
                  ) : (
                    <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
                      Refresh this source, then enable the entries you want
                      available to presets and defaults.
                    </div>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>
        ) : (
          <Card>
            <CardContent className="flex flex-col items-center justify-center gap-3 py-10 text-center">
              <div className="space-y-1">
                <p className="text-sm font-medium">No custom sources yet</p>
                <p className="text-sm text-muted-foreground">
                  Use custom sources only when you need a user-defined endpoint
                  beyond the platform provider cards and shared platform
                  catalog.
                </p>
              </div>
            </CardContent>
          </Card>
        )}
      </section>

      <AgentCredentialsDialog
        isOpen={credentialsProvider !== null}
        onClose={() => setCredentialsProvider(null)}
        onSuccess={handleProviderCredentialsSaved}
        provider={credentialsProvider}
        providerConfigured={selectedCredentialsConfigured}
      />

      <CustomProviderDialog
        provider={editingProvider}
        open={customProviderDialogOpen}
        onOpenChange={(open) => {
          setCustomProviderDialogOpen(open)
          if (!open) {
            setEditingProvider(null)
          }
        }}
      />

      <AlertDialog
        onOpenChange={(open) => {
          if (!open) {
            setDeletingProvider(null)
          }
        }}
        open={deletingProvider !== null}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete custom source</AlertDialogTitle>
            <AlertDialogDescription>
              {deletingProvider
                ? `Delete ${deletingProvider.display_name} from this organization? Any models discovered from this source will stop being available for selection.`
                : "Delete this custom source from this organization?"}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (deletingProvider) {
                  void handleDeleteCustomProvider(deletingProvider)
                }
                setDeletingProvider(null)
              }}
              variant="destructive"
            >
              Delete source
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <CloudCatalogModelDialog
        entry={cloudModelDialog?.entry ?? null}
        onOpenChange={(open) => {
          if (!open) {
            setCloudModelDialog(null)
          }
        }}
        open={cloudModelDialog !== null}
        provider={cloudModelDialog?.provider ?? null}
      />

      <AlertDialog
        onOpenChange={(open) => {
          if (!open) {
            setDeletingCatalogEntry(null)
          }
        }}
        open={deletingCatalogEntry !== null}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete catalog model</AlertDialogTitle>
            <AlertDialogDescription>
              {deletingCatalogEntry
                ? `Delete ${deletingCatalogEntry.model_name} from the catalog? Any workspace access rows for this model will also be removed.`
                : "Delete this catalog entry?"}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={deleteCatalogEntryMutation.isPending}
              onClick={() => {
                if (deletingCatalogEntry) {
                  void handleConfirmDeleteCatalogEntry(deletingCatalogEntry)
                }
              }}
              variant="destructive"
            >
              Delete model
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
