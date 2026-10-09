"use client"

import { Check, ChevronsUpDown } from "lucide-react"
import {
  type ComponentPropsWithoutRef,
  forwardRef,
  type ReactNode,
  useState,
} from "react"
import type { AgentCatalogRead, AgentCustomProviderRead } from "@/client"
import { getModelProviderIconId, ProviderIcon } from "@/components/icons"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { cn } from "@/lib/utils"

/** A model available to agent configuration surfaces. */
export interface EnabledModelOption {
  catalogId: string
  sourceId: string | null
  modelName: string
  modelProvider: string
  iconId: string
  displayName: string
  label: string
  metadata: string
  sourceName: string
  sourceType: string
  baseUrl?: string | null
}

/** Stored fields used to resolve an enabled or legacy model. */
export interface AgentModelSelection {
  catalogId?: string | null
  sourceId?: string | null
  modelProvider?: string | null
  modelName?: string | null
  baseUrl?: string | null
}

/**
 * Props for the shared controlled model combobox. Other button attributes and
 * the ref are forwarded to the trigger, so form labels can target it.
 */
export interface AgentModelComboboxProps
  extends Omit<
    ComponentPropsWithoutRef<"button">,
    "value" | "onChange" | "className" | "children"
  > {
  options: EnabledModelOption[]
  value: AgentModelSelection
  onChange: (option: EnabledModelOption) => void
  /** False while options load, so a saved value is not shown as unavailable. */
  loaded?: boolean
  /** Match a value without a catalog id by name and provider in any source. */
  matchAnySource?: boolean
  placeholder?: string
  unavailableLabel?: string
  triggerClassName?: string
  contentClassName?: string
}

function getSourceType(model: AgentCatalogRead): string {
  if (model.custom_provider_id != null) return "custom"
  return model.organization_id ? "organization" : "platform"
}

/** Build sorted display options from the model catalog and custom sources. */
export function buildEnabledModelOptions(
  models: AgentCatalogRead[] | undefined,
  providers: AgentCustomProviderRead[] | undefined
): EnabledModelOption[] {
  const providersById = new Map(
    (providers ?? []).map((provider) => [provider.id, provider])
  )
  return (models ?? [])
    .map((model) => {
      const provider = model.custom_provider_id
        ? (providersById.get(model.custom_provider_id) ?? null)
        : null
      const isCustomSource = model.custom_provider_id != null
      const sourceName = isCustomSource
        ? (provider?.display_name ?? "Custom")
        : getProviderDisplayLabel(model.model_provider)
      return {
        catalogId: model.id,
        sourceId: model.custom_provider_id,
        modelName: model.model_name,
        modelProvider: model.model_provider,
        iconId: getModelProviderIconId(model.model_provider),
        displayName: model.model_name,
        label: model.model_name,
        metadata: model.model_provider,
        sourceName,
        sourceType: getSourceType(model),
        baseUrl: provider?.base_url ?? null,
      }
    })
    .sort(
      (left, right) =>
        left.sourceName.localeCompare(right.sourceName) ||
        left.displayName.localeCompare(right.displayName)
    )
}

function getProviderDisplayLabel(provider: string): string {
  const labels: Record<string, string> = {
    anthropic: "Anthropic",
    azure_ai: "Azure AI",
    azure_openai: "Azure OpenAI",
    bedrock: "AWS Bedrock",
    gemini: "Google Gemini",
    mistral: "Mistral AI",
    openai: "OpenAI",
    vertex_ai: "Google Vertex AI",
    "custom-model-provider": "Custom",
  }
  return (
    labels[provider] ??
    provider
      .split(/[_\s-]+/)
      .filter(Boolean)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ")
  )
}

function normalizeOptional(value: string | null | undefined) {
  const trimmed = value?.trim()
  return trimmed ? trimmed : null
}

/**
 * Match stored model fields by catalog id first, then compatible legacy fields.
 * With `matchAnySource`, a selection without a catalog id matches the first
 * option with the same name and provider, whatever its source, and a catalog
 * id that matches nothing is unavailable rather than matched by name.
 */
export function findEnabledModelOption(
  options: EnabledModelOption[],
  selection: AgentModelSelection,
  { matchAnySource = false }: { matchAnySource?: boolean } = {}
): EnabledModelOption | null {
  if (selection.catalogId) {
    const match = options.find(
      (option) => option.catalogId === selection.catalogId
    )
    if (match) return match
  }
  if (!selection.modelProvider || !selection.modelName) return null
  if (matchAnySource) {
    // The caller stores no source fields, so the catalog id is the only exact
    // identity: a stale one must not resolve to another row.
    if (selection.catalogId) return null
    return (
      options.find(
        (option) =>
          option.modelProvider === selection.modelProvider &&
          option.modelName === selection.modelName
      ) ?? null
    )
  }
  const sourceId = selection.sourceId ?? null
  const baseUrl = normalizeOptional(selection.baseUrl)
  return (
    options.find(
      (option) =>
        option.sourceId === sourceId &&
        option.modelProvider === selection.modelProvider &&
        option.modelName === selection.modelName
    ) ??
    options.find(
      (option) =>
        option.modelProvider === selection.modelProvider &&
        option.modelName === selection.modelName &&
        normalizeOptional(option.baseUrl) === baseUrl
    ) ??
    null
  )
}

function ModelLabel({
  iconId,
  name,
  detail,
}: {
  iconId: string
  name: string
  detail: string
}) {
  return (
    <span className="flex min-w-0 items-center gap-2">
      <ProviderIcon
        inline
        providerId={iconId}
        className="size-4 shrink-0 rounded-none bg-transparent p-0"
      />
      <span className="truncate" title={name}>
        {name}
      </span>
      <span className="shrink-0 text-muted-foreground">{detail}</span>
    </span>
  )
}

/** Searchable controlled selector shared by preset and workflow agent forms. */
export const AgentModelCombobox = forwardRef<
  HTMLButtonElement,
  AgentModelComboboxProps
>(function AgentModelCombobox(
  {
    options,
    value,
    onChange,
    loaded = true,
    matchAnySource = false,
    disabled = false,
    placeholder = "Select a model",
    unavailableLabel = "Legacy",
    triggerClassName,
    contentClassName,
    ...triggerProps
  },
  ref
) {
  const [open, setOpen] = useState(false)
  const selected = findEnabledModelOption(options, value, { matchAnySource })
  let label: ReactNode = placeholder
  if (selected) {
    label = (
      <ModelLabel
        iconId={selected.iconId}
        name={selected.displayName}
        detail={selected.sourceName}
      />
    )
  } else if (loaded && value.modelProvider && value.modelName) {
    label = (
      <ModelLabel
        iconId={getModelProviderIconId(value.modelProvider)}
        name={`${value.modelProvider} / ${value.modelName}`}
        detail={unavailableLabel}
      />
    )
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          {...triggerProps}
          ref={ref}
          type="button"
          role="combobox"
          aria-expanded={open}
          className={cn(
            "flex h-8 min-w-0 flex-1 items-center justify-between whitespace-nowrap rounded-md border border-input bg-transparent px-3 py-2 text-xs shadow-none focus:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
            !selected && "text-muted-foreground",
            triggerClassName
          )}
          disabled={disabled}
        >
          {label}
          <ChevronsUpDown className="ml-2 size-4 shrink-0 opacity-50" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        sideOffset={4}
        className={cn(
          "w-[var(--radix-popover-trigger-width)] min-w-64 max-w-[min(32rem,calc(100vw-2rem))] p-0",
          contentClassName
        )}
      >
        <Command
          // Start on the selected model so reopening scrolls to it.
          defaultValue={selected ? modelOptionKey(selected) : undefined}
          filter={(key, search) => {
            const option = options.find(
              (candidate) => modelOptionKey(candidate) === key
            )
            if (!option) return 0
            return `${option.displayName} ${option.modelName} ${option.sourceName} ${option.modelProvider}`
              .toLowerCase()
              .includes(search.toLowerCase())
              ? 1
              : 0
          }}
        >
          <CommandInput placeholder="Search models..." />
          <CommandList>
            <CommandEmpty>No models match the search.</CommandEmpty>
            <CommandGroup>
              {options.map((option) => (
                <CommandItem
                  key={modelOptionKey(option)}
                  value={modelOptionKey(option)}
                  onSelect={() => {
                    onChange(option)
                    setOpen(false)
                  }}
                  className="flex items-center gap-2"
                >
                  <ProviderIcon
                    providerId={option.iconId}
                    className="size-4 shrink-0 rounded-none bg-transparent p-0"
                  />
                  <span className="min-w-0 truncate" title={option.displayName}>
                    {option.displayName}
                  </span>
                  <span className="shrink-0 text-[11px] text-muted-foreground">
                    {option.sourceName}
                  </span>
                  <Check
                    className={cn(
                      "ml-auto size-4 shrink-0",
                      selected?.catalogId === option.catalogId
                        ? "opacity-100"
                        : "opacity-0"
                    )}
                  />
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
})

// Platform and organization rows can share a provider and model name.
function modelOptionKey(option: EnabledModelOption): string {
  return option.catalogId
}
