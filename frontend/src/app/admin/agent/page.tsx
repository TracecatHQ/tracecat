"use client"

import { useMemo, useState } from "react"
import { ProviderIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import { Input } from "@/components/ui/input"
import { useAdminPlatformCatalog } from "@/hooks/use-admin"

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

function getProviderLabel(provider?: string | null): string {
  if (!provider) {
    return "Unknown"
  }
  switch (provider) {
    case "anthropic":
      return "Anthropic"
    case "azure_ai":
      return "Azure AI"
    case "azure_openai":
      return "Azure OpenAI"
    case "bedrock":
      return "Bedrock"
    case "gemini":
      return "Gemini"
    case "openai":
      return "OpenAI"
    case "vertex_ai":
      return "Vertex AI"
    default:
      return provider.replace(/_/g, " ")
  }
}

function getModelLabel(model: { model_name?: string | null }): string {
  return model.model_name || "Unnamed model"
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

export default function AdminAgentPage() {
  const [query, setQuery] = useState("")
  const { catalog, isLoading, error } = useAdminPlatformCatalog({ query })
  const providerSections = useMemo(() => {
    const grouped = new Map<
      string,
      {
        provider: string
        models: NonNullable<typeof catalog>["models"]
      }
    >()
    for (const model of catalog?.models ?? []) {
      const provider = model.model_provider || "unknown"
      const current = grouped.get(provider)
      if (current) {
        current.models.push(model)
        continue
      }
      grouped.set(provider, {
        provider,
        models: [model],
      })
    }
    return Array.from(grouped.values())
      .map((section) => ({
        ...section,
        models: [...section.models].sort((left, right) =>
          getModelLabel(left).localeCompare(getModelLabel(right))
        ),
      }))
      .sort((left, right) =>
        getProviderLabel(left.provider).localeCompare(
          getProviderLabel(right.provider)
        )
      )
  }, [catalog?.models])
  const loadedModelCount = catalog?.models.length ?? 0

  if (isLoading) {
    return <CenteredSpinner />
  }

  if (error) {
    return (
      <div className="text-center text-muted-foreground">
        Failed to load the platform catalog.
      </div>
    )
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Agent models
            </h2>
            <p className="text-base text-muted-foreground">
              Review the shared platform model library loaded from the committed
              catalog snapshot during startup.
            </p>
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
          <div className="rounded-md border p-4">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Status
            </p>
            <p className="mt-2 text-sm">
              {formatStatus(catalog?.discovery_status)}
            </p>
          </div>
          <div className="rounded-md border p-4">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Last refreshed
            </p>
            <p className="mt-2 text-sm">
              {formatDateTime(catalog?.last_refreshed_at)}
            </p>
          </div>
          <div className="rounded-md border p-4">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Models loaded
            </p>
            <p className="mt-2 text-sm">{loadedModelCount}</p>
          </div>
        </div>

        {catalog?.last_error ? (
          <div className="rounded-md border border-destructive/40 p-4 text-sm text-destructive">
            {catalog.last_error}
          </div>
        ) : null}

        <section className="space-y-4">
          <div className="space-y-2">
            <h3 className="text-lg font-semibold tracking-tight">
              Platform catalog
            </h3>
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search models or providers"
            />
            <p className="text-xs text-muted-foreground">
              Showing all {loadedModelCount} platform models, grouped by
              provider and sorted by model name.
            </p>
          </div>

          <div className="overflow-hidden rounded-xl border">
            {providerSections.length ? (
              <Accordion type="multiple" className="w-full py-2">
                {providerSections.map((section) => (
                  <AccordionItem
                    key={section.provider}
                    value={section.provider}
                    className="border-none px-5"
                  >
                    <AccordionTrigger className="py-4 hover:no-underline">
                      <div className="flex min-w-0 items-center gap-3 text-left">
                        <ProviderIcon
                          className="size-6 rounded-sm p-0.5"
                          providerId={getProviderIconId(section.provider)}
                        />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium">
                            {getProviderLabel(section.provider)}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {section.models.length} models
                          </p>
                        </div>
                      </div>
                    </AccordionTrigger>
                    <AccordionContent className="pt-0">
                      <div className="pb-4">
                        <div className="hidden grid-cols-[minmax(0,1fr)_88px_88px_72px] gap-4 px-3 py-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground sm:grid">
                          <span>Model</span>
                          <span className="text-right">Context</span>
                          <span className="text-right">Output</span>
                          <span className="text-right">Mode</span>
                        </div>
                        {section.models.map((model) => (
                          <div
                            key={model.id}
                            className="grid min-w-0 grid-cols-1 gap-2 rounded-md px-3 py-2 transition-colors hover:bg-muted/30 sm:grid-cols-[minmax(0,1fr)_88px_88px_72px] sm:items-center sm:gap-4"
                          >
                            <div className="min-w-0">
                              <p className="truncate text-sm font-medium">
                                {getModelLabel(model)}
                              </p>
                              <p className="text-xs text-muted-foreground sm:hidden">
                                {getModelContextLabel(model)} ctx {"·"}{" "}
                                {getModelOutputLabel(model)} out {"·"}{" "}
                                {getModelModeLabel(model)}
                              </p>
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
                          </div>
                        ))}
                      </div>
                    </AccordionContent>
                  </AccordionItem>
                ))}
              </Accordion>
            ) : (
              <div className="px-4 py-6 text-sm text-muted-foreground">
                No platform models matched this search.
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  )
}
