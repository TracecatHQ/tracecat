"use client"

import { format, parseISO } from "date-fns"
import { Check, ChevronsUpDown } from "lucide-react"
import {
  type ComponentPropsWithoutRef,
  forwardRef,
  type ReactNode,
  useState,
} from "react"
import type {
  AgentCatalogRead,
  ModelLifecycleRead,
  WorkspaceAgentModelRead,
} from "@/client"
import { LogoTile } from "@/components/agents/agent-preset-rail-primitives"
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
  lifecycle: ModelLifecycleRead | null
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
  /**
   * Why the options are missing, such as loading or a failed request. When set
   * and `loaded` is false, a saved value stays in the trigger with this text
   * beside it instead of giving way to the placeholder.
   */
  unverifiedLabel?: string
  /** Match a value without a catalog id by name and provider in any source. */
  matchAnySource?: boolean
  placeholder?: string
  unavailableLabel?: string
  triggerClassName?: string
  contentClassName?: string
  /** Property-row trigger: logo tile and model name, no source or chevron. */
  inline?: boolean
}

function getSourceType(model: AgentCatalogRead): string {
  if (model.custom_provider_id != null) return "custom"
  return model.organization_id ? "organization" : "platform"
}

/** Build sorted display options from a workspace's labelled models. */
export function buildEnabledModelOptions(
  models: WorkspaceAgentModelRead[] | undefined
): EnabledModelOption[] {
  return (models ?? [])
    .map((model) => {
      const isCustomSource = model.custom_provider_id != null
      const sourceName = isCustomSource
        ? (model.custom_provider_name ?? "Custom")
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
        baseUrl: model.custom_provider_base_url ?? null,
        lifecycle: model.lifecycle ?? null,
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

/**
 * Short provider-deprecation note for a model, or null while it is current.
 * A retired model runs on its replacement only when the workspace enables it.
 */
export function getModelLifecycleNote(
  option: EnabledModelOption,
  options: EnabledModelOption[]
): string | null {
  const lifecycle = option.lifecycle
  if (!lifecycle) return null
  if (lifecycle.status === "retired") {
    const { replacement } = lifecycle
    if (!replacement) return "Retired"
    const enabled = findEnabledModelOption(
      options,
      { modelProvider: option.modelProvider, modelName: replacement },
      { matchAnySource: true }
    )
    return enabled
      ? `Retired · runs on ${replacement}`
      : `Retired · enable ${replacement}`
  }
  return `Deprecated · retires ${format(parseISO(lifecycle.retires_on), "MMM d, yyyy")}`
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
  inline = false,
  status,
  lifecycleNote,
}: {
  iconId: string
  name: string
  detail: string
  inline?: boolean
  /** Inline only: a muted note such as "Legacy" after the name. */
  status?: string
  lifecycleNote?: string | null
}) {
  if (inline) {
    return (
      <span className="flex min-w-0 items-center gap-2">
        <LogoTile>
          <ProviderIcon
            inline
            providerId={iconId}
            className="size-3 rounded-none bg-transparent p-0"
          />
        </LogoTile>
        <span className="truncate" title={name}>
          {name}
        </span>
        {status ? (
          <span className="shrink-0 text-muted-foreground">{status}</span>
        ) : null}
        {lifecycleNote ? (
          <span className="shrink-0 text-muted-foreground">
            {lifecycleNote}
          </span>
        ) : null}
      </span>
    )
  }
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
      {lifecycleNote ? (
        <span className="shrink-0 text-muted-foreground">{lifecycleNote}</span>
      ) : null}
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
    unverifiedLabel,
    matchAnySource = false,
    disabled = false,
    placeholder = "Select a model",
    unavailableLabel = "Legacy",
    triggerClassName,
    contentClassName,
    inline = false,
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
        inline={inline}
        lifecycleNote={getModelLifecycleNote(selected, options)}
      />
    )
  } else if (value.modelProvider && value.modelName) {
    // Without options the saved value cannot be called unavailable.
    const detail = loaded ? unavailableLabel : unverifiedLabel
    if (detail) {
      label = (
        <ModelLabel
          iconId={getModelProviderIconId(value.modelProvider)}
          name={
            inline
              ? value.modelName
              : `${value.modelProvider} / ${value.modelName}`
          }
          detail={detail}
          inline={inline}
          status={detail}
        />
      )
    }
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
          {inline ? null : (
            <ChevronsUpDown className="ml-2 size-4 shrink-0 opacity-50" />
          )}
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
              {options.map((option) => {
                const lifecycleNote = getModelLifecycleNote(option, options)
                const isSelected = selected?.catalogId === option.catalogId
                return (
                  <CommandItem
                    key={modelOptionKey(option)}
                    value={modelOptionKey(option)}
                    // A retired model stays visible while selected, never newly picked.
                    disabled={
                      option.lifecycle?.status === "retired" && !isSelected
                    }
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
                    <span
                      className="min-w-0 truncate"
                      title={option.displayName}
                    >
                      {option.displayName}
                    </span>
                    <span className="shrink-0 text-[11px] text-muted-foreground">
                      {option.sourceName}
                    </span>
                    {lifecycleNote ? (
                      <span className="shrink-0 text-[11px] text-muted-foreground">
                        {lifecycleNote}
                      </span>
                    ) : null}
                    <Check
                      className={cn(
                        "ml-auto size-4 shrink-0",
                        isSelected ? "opacity-100" : "opacity-0"
                      )}
                    />
                  </CommandItem>
                )
              })}
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
