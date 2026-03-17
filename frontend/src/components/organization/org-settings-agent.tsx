"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { type ReactNode, useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import {
  type ManualDiscoveredModel as AgentManualDiscoveredModel,
  agentDisableModel,
  agentDisableModels,
  agentEnableModel,
  agentEnableModels,
  agentGetDefaultModel,
  agentSetDefaultModel,
  agentUpdateEnabledModelConfig,
  type BuiltInCatalogEntry,
  type DefaultModelSelection,
  type DefaultModelSelectionUpdate,
  type EnabledModelOperation,
  type EnabledModelRuntimeConfigUpdate,
  type EnabledModelsBatchOperation,
  type ModelCatalogEntry,
  type ModelSelection,
} from "@/client"
import { ProviderIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { AgentCredentialsDialog } from "@/components/organization/org-agent-credentials-dialog"
import { mergeCustomSourceRows } from "@/components/organization/org-settings-agent-utils"
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
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { useToast } from "@/components/ui/use-toast"
import { useDebounce } from "@/hooks"
import { useEntitlements } from "@/hooks/use-entitlements"
import {
  type AgentModelSourceRead,
  type BuiltInProviderRead,
  getModelSelectionKey,
  isSameModelSelection as hasSameModelSelection,
  listAllBuiltInAgentCatalog,
  useAgentCatalogMutations,
  useAgentCredentials,
  useAgentModelSources,
  useAgentModels,
  useBuiltInAgentCatalog,
  useModelProviders,
} from "@/lib/hooks"
import { cn } from "@/lib/utils"

const CUSTOM_SOURCE_TYPE_OPTIONS = [
  {
    description:
      "Use this for Ollama, vLLM, LiteLLM, or another OpenAI-style endpoint.",
    label: "OpenAI-compatible gateway",
    value: "openai_compatible_gateway",
  },
  {
    description:
      "Declare a small curated model list when automatic discovery is weak or unavailable.",
    label: "Manual custom source",
    value: "manual_custom",
  },
] as const

const CUSTOM_SOURCE_FLAVOR_OPTIONS = {
  manual_custom: [{ label: "Manual", value: "manual" }],
  openai_compatible_gateway: [
    { label: "Generic OpenAI-compatible", value: "generic_openai_compatible" },
    { label: "Ollama", value: "ollama" },
    { label: "vLLM", value: "vllm" },
    { label: "LiteLLM", value: "litellm" },
  ],
} as const

const customSourceFormSchema = z
  .object({
    apiKey: z.string().optional(),
    apiKeyHeader: z.string().optional(),
    apiVersion: z.string().optional(),
    baseUrl: z.union([z.string().url(), z.literal(""), z.undefined()]),
    declaredModelsText: z.string().optional(),
    displayName: z.string().min(1, "Name is required"),
    flavor: z.string().optional(),
    type: z.enum(["openai_compatible_gateway", "manual_custom"]),
  })
  .superRefine((value, ctx) => {
    if (value.type === "openai_compatible_gateway" && !value.baseUrl?.trim()) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Base URL is required for an OpenAI-compatible source.",
        path: ["baseUrl"],
      })
    }
    if (value.type === "manual_custom" && !value.declaredModelsText?.trim()) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Add at least one declared model.",
        path: ["declaredModelsText"],
      })
    }
  })

type CustomSourceFormValues = z.infer<typeof customSourceFormSchema>

function getErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message) {
    return error.message
  }
  return "Something went wrong."
}

function formatDateTime(value?: string | null): string {
  if (!value) {
    return "Never"
  }
  return new Date(value).toLocaleString()
}

function formatLabel(value: string): string {
  return value.replaceAll("_", " ")
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

function getProviderIconId(provider: string): string {
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
      return "custom-model-provider"
  }
}

function getCustomSourceTypeLabel(type: string): string {
  switch (type) {
    case "manual_custom":
      return "Manual custom"
    case "openai_compatible_gateway":
      return "OpenAI-compatible"
    default:
      return formatLabel(type)
  }
}

function getCustomSourceFlavorLabel(flavor?: string | null): string | null {
  if (!flavor) {
    return null
  }
  switch (flavor) {
    case "generic_openai_compatible":
      return "Generic OpenAI-compatible"
    case "litellm":
      return "LiteLLM"
    case "ollama":
      return "Ollama"
    case "vllm":
      return "vLLM"
    case "manual":
      return "Manual"
    default:
      return formatLabel(flavor)
  }
}

function getCustomSourceIconId(
  source: Pick<AgentModelSourceRead, "type" | "flavor">
): string {
  switch (source.flavor) {
    case "ollama":
      return "ollama"
    case "litellm":
      return "litellm"
    case "vllm":
      return "vllm"
    case "manual":
      return "manual-custom-source"
    case "generic_openai_compatible":
      return "custom"
    default:
      return source.type === "manual_custom" ? "manual-custom-source" : "custom"
  }
}

function normalizeSourceName(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[\s_-]+/g, "")
}

function getCustomModelIconId(
  model: Pick<
    ModelCatalogEntry,
    "metadata" | "model_name" | "source_name" | "source_type"
  >
): string {
  const sourceFlavor =
    model.metadata && typeof model.metadata.source_flavor === "string"
      ? model.metadata.source_flavor
      : null
  switch (sourceFlavor) {
    case "ollama":
      return "ollama"
    case "litellm":
      return "litellm"
    case "vllm":
      return "vllm"
    case "manual":
      return "manual-custom-source"
    case "generic_openai_compatible":
      return "custom"
    default:
      break
  }

  const normalizedSourceName = normalizeSourceName(
    model.source_name ?? model.model_name
  )
  if (normalizedSourceName.includes("ollama")) {
    return "ollama"
  }
  if (normalizedSourceName.includes("vllm")) {
    return "vllm"
  }
  if (normalizedSourceName.includes("litellm")) {
    return "litellm"
  }
  if (model.source_type === "manual_custom") {
    return "manual-custom-source"
  }
  return "custom"
}

function getCatalogModelIconId(
  model: Pick<
    ModelCatalogEntry,
    "metadata" | "model_name" | "model_provider" | "source_name" | "source_type"
  >
): string {
  if (
    model.source_type === "openai_compatible_gateway" ||
    model.source_type === "manual_custom"
  ) {
    return getCustomModelIconId(model)
  }
  return getProviderIconId(model.model_provider)
}

function getCustomSourceDefaults(
  source?: AgentModelSourceRead | null
): CustomSourceFormValues {
  return {
    apiKey: "",
    apiKeyHeader: source?.api_key_header ?? "",
    apiVersion: source?.api_version ?? "",
    baseUrl: source?.base_url ?? "",
    declaredModelsText: serializeDeclaredModels(source?.declared_models),
    displayName: source?.display_name ?? "",
    flavor: source?.flavor ?? "",
    type:
      source?.type === "manual_custom"
        ? "manual_custom"
        : "openai_compatible_gateway",
  }
}

function parseDeclaredModels(value?: string): AgentManualDiscoveredModel[] {
  return (value ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [modelNameRaw, displayNameRaw, providerRaw] = line
        .split("|")
        .map((part) => part.trim())
      return {
        display_name: displayNameRaw || undefined,
        model_name: modelNameRaw,
        model_provider: providerRaw || undefined,
      }
    })
}

function serializeDeclaredModels(
  models?: AgentManualDiscoveredModel[] | null
): string {
  if (!models?.length) {
    return ""
  }
  return models
    .map((model) =>
      [
        model.model_name,
        model.display_name ?? "",
        model.model_provider ?? "",
      ].join(" | ")
    )
    .join("\n")
}

function normalizeNullableInput(value?: string): string | null {
  const trimmed = value?.trim()
  return trimmed ? trimmed : null
}

function normalizeOptionalInput(value?: string): string | undefined {
  const trimmed = value?.trim()
  return trimmed ? trimmed : undefined
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

function getModelSourceLabel(
  model: Pick<ModelCatalogEntry, "source_id" | "source_name">
): string {
  return model.source_name ?? (model.source_id ? "Custom" : "Platform")
}

function _ModelRow({
  disabled,
  model,
  onToggle,
}: {
  disabled: boolean
  model: ModelCatalogEntry
  onToggle: (model: ModelCatalogEntry) => Promise<void>
}) {
  return (
    <div className="flex flex-col gap-3 rounded-lg border p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-medium">{getModelLabel(model)}</p>
          <Badge variant="outline">{model.model_provider}</Badge>
          {model.base_url ? <Badge variant="outline">Custom URL</Badge> : null}
        </div>
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
  onUpdateConfig,
  onToggle,
}: {
  disabled: boolean
  model: BuiltInCatalogEntry
  onUpdateConfig: (
    model: BuiltInCatalogEntry,
    config: {
      bedrockInferenceProfileId?: string | null
    }
  ) => Promise<void>
  onToggle: (model: ModelCatalogEntry) => Promise<void>
}) {
  const [bedrockInferenceProfileId, setBedrockInferenceProfileId] = useState(
    model.enabled_config?.bedrock_inference_profile_id ?? ""
  )
  const canEnable = canEnableBuiltInCatalogModel(model)
  const isEnabledBedrock = model.enabled && model.model_provider === "bedrock"
  const savedBedrockInferenceProfileId =
    model.enabled_config?.bedrock_inference_profile_id ?? ""
  const hasPendingBedrockConfigChange =
    bedrockInferenceProfileId.trim() !== savedBedrockInferenceProfileId
  let statusMessage = model.readiness_message
  if (!statusMessage && model.runtime_target_configured === false) {
    statusMessage =
      "Finish provider setup before allowing this model for selection."
  }

  useEffect(() => {
    setBedrockInferenceProfileId(savedBedrockInferenceProfileId)
  }, [
    savedBedrockInferenceProfileId,
    model.model_name,
    model.model_provider,
    model.source_id,
  ])

  return (
    <div className="border-b border-border/40 py-3 last:border-b-0">
      <div className="flex flex-col gap-3">
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_88px_88px_72px_auto] sm:items-center sm:gap-4">
          <div className="min-w-0 space-y-1">
            <p className="truncate text-sm font-medium">
              {getModelLabel(model)}
            </p>
            <p className="text-xs text-muted-foreground sm:hidden">
              {getModelContextLabel(model)} ctx {"·"}{" "}
              {getModelOutputLabel(model)} out {"·"} {getModelModeLabel(model)}
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
        {isEnabledBedrock ? (
          <div className="rounded-md border border-border/60 px-3 py-3">
            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
              <div className="space-y-2">
                <div className="space-y-1">
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Inference profile
                  </p>
                  <p className="text-xs text-muted-foreground">
                    Optional for Bedrock models that need a profile ID or ARN
                    instead of direct model invocation.
                  </p>
                </div>
                <Input
                  disabled={disabled}
                  onChange={(event) => {
                    setBedrockInferenceProfileId(event.target.value)
                  }}
                  placeholder="Optional Bedrock inference profile ID or ARN"
                  value={bedrockInferenceProfileId}
                />
              </div>
              <Button
                disabled={disabled || !hasPendingBedrockConfigChange}
                onClick={() => {
                  void onUpdateConfig(model, {
                    bedrockInferenceProfileId: normalizeNullableInput(
                      bedrockInferenceProfileId
                    ),
                  })
                }}
                size="sm"
                type="button"
                variant="outline"
              >
                Save
              </Button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  )
}

function ProviderConnectionItem({
  disabled,
  enabledCount,
  onDisableAllModels,
  onEnableAllModels,
  isExpanded,
  onConfigureProvider,
  onUpdateModelConfig,
  onDeleteCredentials,
  onExpandedChange,
  onToggleModel,
  provider,
}: {
  disabled: boolean
  enabledCount: number
  onDisableAllModels: (provider: string, label: string) => Promise<void>
  onEnableAllModels: (provider: string, label: string) => Promise<void>
  isExpanded: boolean
  onConfigureProvider: (provider: string) => void
  onUpdateModelConfig: (
    model: BuiltInCatalogEntry,
    config: {
      bedrockInferenceProfileId?: string | null
    }
  ) => Promise<void>
  onDeleteCredentials: (provider: string, label: string) => Promise<void>
  onExpandedChange: (expanded: boolean) => void
  onToggleModel: (model: ModelCatalogEntry) => Promise<void>
  provider: BuiltInProviderRead
}) {
  const [catalogQueryInput, setCatalogQueryInput] = useState("")
  const [allowAllOverride, setAllowAllOverride] = useState<boolean | null>(null)
  const [enabledCountOverride, setEnabledCountOverride] = useState<
    number | null
  >(null)
  const [isWhitelistMode, setIsWhitelistMode] = useState(false)
  const [catalogQuery] = useDebounce(catalogQueryInput.trim(), 250)
  const { data: allProviderModels, isLoading: providerAccessLoading } =
    useQuery<BuiltInCatalogEntry[], Error>({
      enabled: isExpanded,
      queryKey: ["agent-models", "builtins", "all", provider.provider],
      queryFn: async () =>
        await listAllBuiltInAgentCatalog({
          provider: provider.provider,
        }),
    })
  const {
    error,
    fetchNextPage,
    hasNextPage,
    inventory,
    isFetchingNextPage,
    isLoading,
  } = useBuiltInAgentCatalog({
    enabled: isExpanded,
    limit: 50,
    provider: provider.provider,
    query: catalogQuery,
  })

  useEffect(() => {
    setCatalogQueryInput("")
    setAllowAllOverride(null)
    setEnabledCountOverride(null)
    setIsWhitelistMode(false)
  }, [provider.provider])

  const discoveredProviderModels = provider.discovered_models ?? []
  const discoveredProviderModelCount = discoveredProviderModels.length
  const providerModels = inventory?.models ?? []
  const selectableProviderModelCount =
    allProviderModels?.filter((model) => canEnableBuiltInCatalogModel(model))
      .length ?? 0
  const resolvedEnabledCount = enabledCount
  const enabledSelectableProviderCount =
    enabledCountOverride ?? resolvedEnabledCount
  const allSelectableAllowed =
    selectableProviderModelCount > 0 &&
    enabledSelectableProviderCount === selectableProviderModelCount
  const allowAllChecked =
    !isWhitelistMode && (allowAllOverride ?? allSelectableAllowed)
  const showWhitelistControls = isWhitelistMode || !allowAllChecked
  const showModelList = provider.provider === "bedrock" || showWhitelistControls
  const showLoadMore =
    hasNextPage &&
    provider.credentials_configured &&
    showModelList &&
    !allowAllChecked &&
    providerModels.length > 0
  const subtitle = provider.credentials_configured
    ? provider.base_url || provider.runtime_target
      ? [provider.base_url, provider.runtime_target].filter(Boolean).join(" · ")
      : "Connected"
    : "Not connected"

  useEffect(() => {
    if (allowAllOverride === null) {
      return
    }
    if (allowAllOverride === allSelectableAllowed) {
      setAllowAllOverride(null)
    }
  }, [allowAllOverride, allSelectableAllowed])

  useEffect(() => {
    if (enabledCountOverride === null) {
      return
    }
    if (enabledCountOverride === resolvedEnabledCount) {
      setEnabledCountOverride(null)
    }
  }, [enabledCountOverride, resolvedEnabledCount])

  return (
    <RbacListItem
      actions={
        <div className="flex flex-wrap items-center justify-end gap-2">
          <ProviderMetaPill active={enabledSelectableProviderCount > 0}>
            {enabledSelectableProviderCount} enabled
          </ProviderMetaPill>
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
      subtitle={subtitle}
      title={provider.label}
    >
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
                <p className="text-sm font-medium">Connect {provider.label}</p>
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
                  allowAllChecked ? "text-foreground" : "text-muted-foreground"
                )}
              >
                Allow all
              </span>
              <Switch
                checked={allowAllChecked}
                disabled={
                  disabled ||
                  isLoading ||
                  providerAccessLoading ||
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
              {providerAccessLoading
                ? "Loading model access…"
                : selectableProviderModelCount
                  ? `${enabledSelectableProviderCount} of ${selectableProviderModelCount} models enabled`
                  : discoveredProviderModelCount
                    ? `${enabledSelectableProviderCount} enabled`
                    : provider.credentials_configured
                      ? "No selectable models available"
                      : "Connect to manage platform models"}
            </ProviderMetaPill>
          </div>
        </div>

        {error ? (
          <AlertNotification level="error" message={error.message} />
        ) : null}

        {isLoading && !inventory ? (
          <CenteredSpinner />
        ) : showModelList ? (
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
                        onUpdateConfig={onUpdateModelConfig}
                        onToggle={async (nextModel) => {
                          const nextCountDelta = nextModel.enabled ? -1 : 1
                          setAllowAllOverride(null)
                          setEnabledCountOverride((current) =>
                            Math.min(
                              selectableProviderModelCount ||
                                Number.POSITIVE_INFINITY,
                              Math.max(
                                0,
                                (current ?? resolvedEnabledCount) +
                                  nextCountDelta
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

        {showLoadMore ? (
          <Button
            disabled={Boolean(isFetchingNextPage)}
            onClick={() => {
              void fetchNextPage()
            }}
            size="sm"
            variant="outline"
          >
            {isFetchingNextPage ? "Loading..." : "Load more"}
          </Button>
        ) : null}
      </div>
    </RbacListItem>
  )
}

function CustomSourceDialog({
  initialSource,
  isPending,
  onClose,
  onCreate,
  onUpdate,
  open,
}: {
  initialSource?: AgentModelSourceRead | null
  isPending: boolean
  onClose: () => void
  onCreate: (values: {
    api_key?: string | null
    api_key_header?: string | null
    api_version?: string | null
    base_url?: string | null
    declared_models?: AgentManualDiscoveredModel[] | null
    display_name: string
    flavor?: string | null
    type: string
  }) => Promise<void>
  onUpdate: (values: {
    api_key?: string | null
    api_key_header?: string | null
    api_version?: string | null
    base_url?: string | null
    declared_models?: AgentManualDiscoveredModel[] | null
    display_name?: string | null
    flavor?: string | null
    sourceId: string
  }) => Promise<void>
  open: boolean
}) {
  const form = useForm<CustomSourceFormValues>({
    defaultValues: getCustomSourceDefaults(initialSource),
    resolver: zodResolver(customSourceFormSchema),
  })

  const selectedType = form.watch("type")
  const availableFlavors =
    selectedType === "manual_custom"
      ? CUSTOM_SOURCE_FLAVOR_OPTIONS.manual_custom
      : CUSTOM_SOURCE_FLAVOR_OPTIONS.openai_compatible_gateway

  useEffect(() => {
    form.reset(getCustomSourceDefaults(initialSource))
  }, [form, initialSource, open])

  async function onSubmit(values: CustomSourceFormValues) {
    const declaredModels =
      values.type === "manual_custom"
        ? parseDeclaredModels(values.declaredModelsText)
        : undefined
    const payload = {
      api_key: initialSource
        ? normalizeOptionalInput(values.apiKey)
        : normalizeNullableInput(values.apiKey),
      api_key_header: normalizeNullableInput(values.apiKeyHeader),
      api_version: normalizeNullableInput(values.apiVersion),
      base_url: normalizeNullableInput(values.baseUrl),
      declared_models: declaredModels?.length ? declaredModels : null,
      display_name: values.displayName.trim(),
      flavor: normalizeNullableInput(values.flavor),
    }

    if (initialSource) {
      await onUpdate({
        ...payload,
        sourceId: initialSource.id,
      })
    } else {
      await onCreate({
        ...payload,
        type: values.type,
      })
    }
    form.reset(getCustomSourceDefaults(null))
    onClose()
  }

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => !nextOpen && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {initialSource ? "Edit custom source" : "Add custom source"}
          </DialogTitle>
          <DialogDescription>
            Add a genuinely user-defined endpoint such as Ollama, vLLM, a
            customer-run LiteLLM, or a small manual model catalog.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form
            className="space-y-5"
            onSubmit={form.handleSubmit((values) => {
              void onSubmit(values).catch(() => {})
            })}
          >
            <FormField
              control={form.control}
              name="type"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Source type</FormLabel>
                  <Select
                    disabled={Boolean(initialSource)}
                    onValueChange={field.onChange}
                    value={field.value}
                  >
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder="Choose a source type" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {CUSTOM_SOURCE_TYPE_OPTIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormDescription>
                    {CUSTOM_SOURCE_TYPE_OPTIONS.find(
                      (option) => option.value === field.value
                    )?.description ??
                      "Choose how this source should be discovered."}
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="grid gap-5 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="displayName"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Name</FormLabel>
                    <FormControl>
                      <Input placeholder="Customer vLLM" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="flavor"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Flavor</FormLabel>
                    <Select
                      onValueChange={(value) =>
                        field.onChange(value === "none" ? "" : value)
                      }
                      value={field.value || "none"}
                    >
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder="Optional" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        <SelectItem value="none">None</SelectItem>
                        {availableFlavors.map((option) => (
                          <SelectItem key={option.value} value={option.value}>
                            {option.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <div className="grid gap-5 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="baseUrl"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Base URL</FormLabel>
                    <FormControl>
                      <Input
                        placeholder="https://gateway.example.com"
                        {...field}
                      />
                    </FormControl>
                    <FormDescription>
                      Required for OpenAI-compatible sources. Optional for
                      manual catalogs.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="apiVersion"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>API version</FormLabel>
                    <FormControl>
                      <Input placeholder="2024-10-21" {...field} />
                    </FormControl>
                    <FormDescription>
                      Only set this when the upstream expects a version header
                      or query parameter.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <div className="grid gap-5 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="apiKey"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>API key</FormLabel>
                    <FormControl>
                      <Input
                        placeholder={
                          initialSource?.api_key_configured
                            ? "Leave blank to keep the saved key"
                            : "Optional"
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
                control={form.control}
                name="apiKeyHeader"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>API key header</FormLabel>
                    <FormControl>
                      <Input placeholder="Authorization" {...field} />
                    </FormControl>
                    <FormDescription>
                      Defaults to `Authorization` when left blank.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            {selectedType === "manual_custom" ? (
              <FormField
                control={form.control}
                name="declaredModelsText"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Declared models</FormLabel>
                    <FormControl>
                      <Textarea
                        className="min-h-40 font-mono text-xs"
                        placeholder={
                          "llama-3.3-70b | Llama 3.3 70B | meta\nmistral-large | Mistral Large | mistral"
                        }
                        {...field}
                      />
                    </FormControl>
                    <FormDescription>
                      One model per line. Use `model_name | display name |
                      provider`.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
            ) : null}

            <DialogFooter>
              <Button onClick={onClose} type="button" variant="outline">
                Cancel
              </Button>
              <Button disabled={isPending} type="submit">
                {initialSource ? "Save source" : "Add source"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

export function OrgSettingsAgentForm() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { hasEntitlement } = useEntitlements()
  const agentAddonsEnabled = hasEntitlement("agent_addons")
  const [sourceCatalogRowsById, setSourceCatalogRowsById] = useState<
    Record<string, ModelCatalogEntry[]>
  >({})
  const [credentialsProvider, setCredentialsProvider] = useState<string | null>(
    null
  )
  const [deletingSource, setDeletingSource] =
    useState<AgentModelSourceRead | null>(null)
  const [editingSource, setEditingSource] =
    useState<AgentModelSourceRead | null>(null)
  const [expandedProvider, setExpandedProvider] = useState<string | null>(null)
  const [isCreateSourceOpen, setIsCreateSourceOpen] = useState(false)
  const { data: defaultModel, error: defaultModelError } = useQuery<
    DefaultModelSelection | null,
    Error
  >({
    queryKey: ["agent-default-model"],
    queryFn: async () => await agentGetDefaultModel(),
  })
  const {
    error: providersError,
    isLoading: providersLoading,
    providers,
  } = useModelProviders()
  const {
    error: sourcesError,
    isLoading: sourcesLoading,
    sources,
  } = useAgentModelSources()
  const { models, modelsError, modelsLoading } = useAgentModels()
  const selectedDefaultCatalogModel = models?.find((model) =>
    hasSameModelSelection(toModelSelection(model), defaultModel)
  )
  const currentDefaultModel =
    selectedDefaultCatalogModel ?? defaultModel ?? null
  const currentDefaultModelKey = currentDefaultModel
    ? getModelSelectionKey({
        source_id: currentDefaultModel.source_id ?? null,
        model_provider: currentDefaultModel.model_provider,
        model_name: currentDefaultModel.model_name,
      })
    : ""
  const modelLookupByKey = new Map(
    (models ?? []).map((model) => [
      getModelSelectionKey(toModelSelection(model)),
      model,
    ])
  )
  const {
    createSource,
    deleteSource,
    isUpdating,
    refreshSource,
    updateSource,
  } = useAgentCatalogMutations()
  const { deleteCredentials } = useAgentCredentials()
  const invalidateAgentModelQueries = () => {
    queryClient.invalidateQueries({ queryKey: ["agent-models"] })
    queryClient.invalidateQueries({ queryKey: ["agent-models", "custom"] })
    queryClient.invalidateQueries({ queryKey: ["agent-models", "builtins"] })
    queryClient.invalidateQueries({ queryKey: ["agent-providers"] })
    queryClient.invalidateQueries({ queryKey: ["agent-sources"] })
    queryClient.invalidateQueries({ queryKey: ["agent-default-model"] })
  }
  const { mutateAsync: enableModel, isPending: isEnablingModel } = useMutation({
    mutationFn: async (selection: EnabledModelOperation) =>
      await agentEnableModel({ requestBody: selection }),
    onSuccess: invalidateAgentModelQueries,
  })
  const { mutateAsync: enableModels, isPending: isEnablingModels } =
    useMutation({
      mutationFn: async (modelsToEnable: EnabledModelsBatchOperation) =>
        await agentEnableModels({ requestBody: modelsToEnable }),
      onSuccess: invalidateAgentModelQueries,
    })
  const { mutateAsync: disableModel, isPending: isDisablingModel } =
    useMutation({
      mutationFn: async (selection: ModelSelection) =>
        await agentDisableModel({
          sourceId: selection.source_id ?? null,
          modelProvider: selection.model_provider,
          modelName: selection.model_name,
        }),
      onSuccess: invalidateAgentModelQueries,
    })
  const { mutateAsync: disableModels, isPending: isDisablingModels } =
    useMutation({
      mutationFn: async (modelsToDisable: EnabledModelsBatchOperation) =>
        await agentDisableModels({ requestBody: modelsToDisable }),
      onSuccess: invalidateAgentModelQueries,
    })
  const { mutateAsync: updateDefaultModel, isPending: defaultModelUpdating } =
    useMutation({
      mutationFn: async (selection: DefaultModelSelectionUpdate) =>
        await agentSetDefaultModel({ requestBody: selection }),
      onSuccess: invalidateAgentModelQueries,
    })
  const {
    mutateAsync: updateEnabledModelConfig,
    isPending: isUpdatingEnabledModelConfig,
  } = useMutation({
    mutationFn: async (requestBody: EnabledModelRuntimeConfigUpdate) =>
      await agentUpdateEnabledModelConfig({ requestBody }),
    onSuccess: invalidateAgentModelQueries,
  })
  const isSelectionUpdating =
    defaultModelUpdating ||
    isEnablingModel ||
    isEnablingModels ||
    isDisablingModel ||
    isDisablingModels ||
    isUpdatingEnabledModelConfig

  async function handleModelToggle(model: ModelCatalogEntry) {
    const selection = toModelSelection(model)
    try {
      if (model.enabled) {
        await disableModel(selection)
      } else {
        await enableModel(selection)
      }
    } catch (error) {
      toast({
        description: getErrorMessage(error),
        title: `Failed to ${model.enabled ? "disable" : "enable"} ${getModelLabel(model)}`,
        variant: "destructive",
      })
    }
  }

  async function handleEnableAllProviderModels(
    provider: string,
    label: string
  ) {
    try {
      const discoveredModels = await listAllBuiltInAgentCatalog({
        provider,
      })
      const modelsToEnable = discoveredModels.filter(
        (model) => canEnableBuiltInCatalogModel(model) && !model.enabled
      )
      if (modelsToEnable.length) {
        await enableModels({
          models: modelsToEnable.map((model) => toModelSelection(model)),
        })
      }

      toast({
        description: modelsToEnable.length
          ? `Every selectable ${label} model is now allowed.`
          : `All selectable ${label} models were already allowed.`,
        title: "Provider access updated",
      })
    } catch (error) {
      toast({
        description: getErrorMessage(error),
        title: `Failed to update ${label} model access`,
        variant: "destructive",
      })
      throw error
    }
  }

  async function handleDisableAllProviderModels(
    provider: string,
    label: string
  ) {
    try {
      const discoveredModels = await listAllBuiltInAgentCatalog({
        provider,
      })
      const modelsToDisable = discoveredModels.filter((model) => model.enabled)
      if (modelsToDisable.length) {
        await disableModels({
          models: modelsToDisable.map((model) => toModelSelection(model)),
        })
      }

      toast({
        description: modelsToDisable.length
          ? `All ${label} models were removed from the allowlist.`
          : `No ${label} models were currently allowed.`,
        title: "Provider access updated",
      })
    } catch (error) {
      toast({
        description: getErrorMessage(error),
        title: `Failed to update ${label} model access`,
        variant: "destructive",
      })
      throw error
    }
  }

  async function handleRefreshSource(source: AgentModelSourceRead) {
    try {
      const rows = await refreshSource(source.id)
      setSourceCatalogRowsById((current) => ({
        ...current,
        [source.id]: rows,
      }))
      toast({
        description: `Updated ${source.display_name}.`,
        title: "Custom source refreshed",
      })
    } catch (error) {
      toast({
        description: getErrorMessage(error),
        title: `Failed to refresh ${source.display_name}`,
        variant: "destructive",
      })
    }
  }

  async function handleDeleteCredentials(provider: string, label: string) {
    if (
      !window.confirm(
        `Delete the saved ${label} credentials for this organization?`
      )
    ) {
      return
    }
    try {
      await deleteCredentials(provider)
      toast({
        description: `${label} credentials were removed.`,
        title: "Credentials deleted",
      })
    } catch (error) {
      toast({
        description: getErrorMessage(error),
        title: `Failed to delete ${label} credentials`,
        variant: "destructive",
      })
    }
  }

  async function handleDeleteSource(source: AgentModelSourceRead) {
    try {
      await deleteSource(source.id)
      setSourceCatalogRowsById((current) => {
        const next = { ...current }
        delete next[source.id]
        return next
      })
      toast({
        description: `${source.display_name} was removed.`,
        title: "Custom source deleted",
      })
    } catch (error) {
      toast({
        description: getErrorMessage(error),
        title: `Failed to delete ${source.display_name}`,
        variant: "destructive",
      })
    }
  }

  async function handleSetDefaultModel(selection: DefaultModelSelectionUpdate) {
    try {
      await updateDefaultModel(selection)
      toast({
        title: "Default model updated",
      })
    } catch (error) {
      toast({
        description: getErrorMessage(error),
        title: "Update failed",
        variant: "destructive",
      })
    }
  }

  async function handleUpdateEnabledModelConfig(
    model: BuiltInCatalogEntry,
    config: {
      bedrockInferenceProfileId?: string | null
    }
  ) {
    try {
      await updateEnabledModelConfig({
        source_id: model.source_id ?? null,
        model_provider: model.model_provider,
        model_name: model.model_name,
        config: {
          bedrock_inference_profile_id: config.bedrockInferenceProfileId,
        },
      })
    } catch (error) {
      toast({
        description: getErrorMessage(error),
        title: `Failed to update ${getModelLabel(model)}`,
        variant: "destructive",
      })
      throw error
    }
  }

  async function handleCreateSource(values: {
    api_key?: string | null
    api_key_header?: string | null
    api_version?: string | null
    base_url?: string | null
    declared_models?: AgentManualDiscoveredModel[] | null
    display_name: string
    flavor?: string | null
    type: string
  }) {
    try {
      await createSource(values)
      toast({
        description: `${values.display_name} is ready for manual refresh.`,
        title: "Custom source created",
      })
    } catch (error) {
      toast({
        description: getErrorMessage(error),
        title: "Failed to create custom source",
        variant: "destructive",
      })
      throw error
    }
  }

  async function handleUpdateSource(values: {
    api_key?: string | null
    api_key_header?: string | null
    api_version?: string | null
    base_url?: string | null
    declared_models?: AgentManualDiscoveredModel[] | null
    display_name?: string | null
    flavor?: string | null
    sourceId: string
  }) {
    try {
      await updateSource(values)
      toast({
        description: "Saved your custom source changes.",
        title: "Custom source updated",
      })
    } catch (error) {
      toast({
        description: getErrorMessage(error),
        title: "Failed to update custom source",
        variant: "destructive",
      })
      throw error
    }
  }

  function handleProviderCredentialsSaved() {
    queryClient.invalidateQueries({ queryKey: ["agent-providers"] })
    queryClient.invalidateQueries({ queryKey: ["agent-models", "builtins"] })
    queryClient.invalidateQueries({ queryKey: ["agent-models"] })
    queryClient.invalidateQueries({ queryKey: ["agent-models", "custom"] })
  }

  if (
    !providers &&
    !sources &&
    !models &&
    (providersLoading || sourcesLoading || modelsLoading)
  ) {
    return <CenteredSpinner />
  }

  return (
    <div className="space-y-12">
      {providersError || sourcesError || modelsError || defaultModelError ? (
        <AlertNotification
          level="error"
          message={
            defaultModelError?.message ||
            providersError?.message ||
            sourcesError?.message ||
            modelsError?.message ||
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
              Choose the organization-wide default from the enabled model
              catalog. Workspaces can optionally narrow that catalog further in
              workspace settings.
            </p>
          </div>
          {!models?.length ? (
            <p className="text-sm text-muted-foreground">
              Enable at least one model from the sections below before choosing
              a default.
            </p>
          ) : (
            <Select
              disabled={isSelectionUpdating || isUpdating || modelsLoading}
              onValueChange={(selectionKey) => {
                const nextModel = modelLookupByKey.get(selectionKey)
                if (
                  !nextModel ||
                  hasSameModelSelection(
                    toModelSelection(nextModel),
                    defaultModel
                  )
                ) {
                  return
                }
                void handleSetDefaultModel(toModelSelection(nextModel))
              }}
              value={currentDefaultModelKey}
            >
              <SelectTrigger className="h-12 px-4 [&>svg]:shrink-0">
                {currentDefaultModel ? (
                  <div className="flex min-w-0 items-center gap-3 text-left">
                    <ProviderIcon
                      className="size-5 rounded-sm p-0.5"
                      providerId={getCatalogModelIconId(currentDefaultModel)}
                    />
                    <div className="min-w-0 space-y-0.5">
                      <span className="block truncate text-sm font-medium text-foreground">
                        {getModelLabel(currentDefaultModel)}
                      </span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {selectedDefaultCatalogModel?.source_name ??
                          currentDefaultModel.source_name ??
                          currentDefaultModel.model_provider}
                      </span>
                    </div>
                  </div>
                ) : (
                  <SelectValue placeholder="Choose a default model" />
                )}
              </SelectTrigger>
              <SelectContent>
                {models.map((model) => {
                  const isSelected = hasSameModelSelection(
                    toModelSelection(model),
                    defaultModel
                  )
                  const modelKey = getModelSelectionKey(toModelSelection(model))
                  return (
                    <SelectItem key={modelKey} value={modelKey}>
                      <div className="flex min-w-0 items-start gap-3 py-1">
                        <ProviderIcon
                          className="mt-0.5 size-5 rounded-sm p-0.5"
                          providerId={getCatalogModelIconId(model)}
                        />
                        <div className="min-w-0 space-y-1">
                          <div className="flex min-w-0 items-center gap-2">
                            <span className="truncate text-sm font-medium">
                              {getModelLabel(model)}
                            </span>
                            {isSelected ? (
                              <span className="shrink-0 text-xs text-muted-foreground">
                                Current default
                              </span>
                            ) : null}
                          </div>
                          <p className="truncate text-xs text-muted-foreground">
                            {getModelSourceLabel(model)}
                          </p>
                        </div>
                      </div>
                    </SelectItem>
                  )
                })}
              </SelectContent>
            </Select>
          )}

          {isSelectionUpdating || isUpdating ? (
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
              : "Configure provider credentials and enable the organization-level models you want available."}
          </p>
        </div>
        <div className="space-y-4">
          {providers?.length ? (
            <RbacListContainer>
              {providers.map((provider) => {
                const enabledCount =
                  models?.filter(
                    (model) =>
                      !model.source_id &&
                      model.model_provider === provider.provider
                  ).length ?? 0
                return (
                  <ProviderConnectionItem
                    disabled={isUpdating}
                    enabledCount={enabledCount}
                    isExpanded={expandedProvider === provider.provider}
                    key={provider.provider}
                    onDisableAllModels={handleDisableAllProviderModels}
                    onEnableAllModels={handleEnableAllProviderModels}
                    onConfigureProvider={setCredentialsProvider}
                    onDeleteCredentials={handleDeleteCredentials}
                    onExpandedChange={(expanded) => {
                      setExpandedProvider(expanded ? provider.provider : null)
                    }}
                    onToggleModel={handleModelToggle}
                    onUpdateModelConfig={handleUpdateEnabledModelConfig}
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
              Add custom sources like Ollama, vLLM, self-hosted LiteLLM, or
              curated manual model lists.
            </p>
          </div>
          <Button onClick={() => setIsCreateSourceOpen(true)} variant="outline">
            Add custom source
          </Button>
        </div>

        {sources?.length ? (
          <div className="space-y-4">
            {sources.map((source) => {
              const sourceModels = mergeCustomSourceRows(
                sourceCatalogRowsById[source.id],
                models,
                source.id
              )
              return (
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
                          </CardDescription>
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <Button
                          onClick={() => setEditingSource(source)}
                          size="sm"
                          variant="outline"
                        >
                          Edit
                        </Button>
                        <Button
                          onClick={() => {
                            void handleRefreshSource(source)
                          }}
                          size="sm"
                          variant="outline"
                        >
                          Refresh
                        </Button>
                        <Button
                          onClick={() => {
                            setDeletingSource(source)
                          }}
                          size="sm"
                          variant="outline"
                        >
                          Delete
                        </Button>
                      </div>
                    </div>
                    <div className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
                      <p>
                        Last refreshed:{" "}
                        {formatDateTime(source.last_refreshed_at)}
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

                    {source.declared_models?.length ? (
                      <div className="rounded-lg border p-4">
                        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                          Declared models
                        </p>
                        <Separator className="my-3" />
                        <div className="space-y-2">
                          {source.declared_models.map((model) => (
                            <p
                              className="text-sm"
                              key={`${source.id}-${model.model_name}`}
                            >
                              {model.display_name || model.model_name}
                              <span className="text-muted-foreground">
                                {" · "}
                                {model.model_name}
                                {model.model_provider
                                  ? ` · ${model.model_provider}`
                                  : ""}
                              </span>
                            </p>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    {source.type === "manual_custom" &&
                    !source.declared_models?.length ? (
                      <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
                        Add declared models to this manual source, then save and
                        enable the entries you want to expose.
                      </div>
                    ) : null}

                    {source.type !== "manual_custom" && !source.base_url ? (
                      <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
                        Set a base URL before refreshing this custom endpoint.
                      </div>
                    ) : null}

                    {sourceModels.length ? (
                      sourceModels.map((model) => (
                        <CustomSourceModelRow
                          disabled={isUpdating || isSelectionUpdating}
                          key={getModelSelectionKey(toModelSelection(model))}
                          model={model}
                          onToggle={handleModelToggle}
                        />
                      ))
                    ) : (
                      <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
                        Refresh this source, then enable the entries you want
                        available to presets and defaults.
                      </div>
                    )}
                  </CardContent>
                </Card>
              )
            })}
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
      />

      <CustomSourceDialog
        isPending={isUpdating}
        onClose={() => setIsCreateSourceOpen(false)}
        onCreate={handleCreateSource}
        onUpdate={handleUpdateSource}
        open={isCreateSourceOpen}
      />

      <CustomSourceDialog
        initialSource={editingSource}
        isPending={isUpdating}
        onClose={() => setEditingSource(null)}
        onCreate={handleCreateSource}
        onUpdate={handleUpdateSource}
        open={editingSource !== null}
      />

      <AlertDialog
        onOpenChange={(open) => {
          if (!open) {
            setDeletingSource(null)
          }
        }}
        open={deletingSource !== null}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete custom source</AlertDialogTitle>
            <AlertDialogDescription>
              {deletingSource
                ? `Delete ${deletingSource.display_name} from this organization? Any models discovered from this source will stop being available for selection.`
                : "Delete this custom source from this organization?"}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (deletingSource) {
                  void handleDeleteSource(deletingSource)
                }
                setDeletingSource(null)
              }}
              variant="destructive"
            >
              Delete source
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
