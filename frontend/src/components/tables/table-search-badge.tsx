"use client"

import { Loader2, TextSearchIcon } from "lucide-react"
import type { TableSearchDisplayState } from "@/client"
import { useTableSearchContext } from "@/components/tables/table-search-context"
import { TableSearchProgress } from "@/components/tables/table-search-progress"
import { Badge } from "@/components/ui/badge"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import type { useTableSearch } from "@/hooks/use-table-search"
import { cn } from "@/lib/utils"

type TableSearchState = Pick<
  ReturnType<typeof useTableSearch>,
  "configuration" | "provider"
>

const STATUS_LABELS: Record<TableSearchDisplayState, string> = {
  disabled: "Off",
  unavailable: "Unavailable",
  indexing: "Indexing",
  ready: "Ready",
  updating: "Indexing",
  needs_attention: "Needs attention",
}

const TONE_CLASS_NAMES: Record<string, string> = {
  "Needs attention":
    "border-transparent bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-100",
  Off: "text-muted-foreground",
  Unavailable: "text-muted-foreground",
}

const PENDING_LABELS = new Set(["Checking status", "Indexing"])

/** Derive the status label shown for a table's semantic search index. */
export function tableSearchStatusLabel({
  configuration,
  provider,
}: TableSearchState) {
  const selected = (configuration.data?.selected_column_ids?.length ?? 0) > 0
  if (configuration.isPending || provider.isPending) return "Checking status"
  if (configuration.error || provider.error) return "Needs attention"
  if (!provider.data?.available || provider.data.state === "paused")
    return "Unavailable"
  if (selected && provider.data.reindex_required) return "Indexing"
  return STATUS_LABELS[configuration.data?.status ?? "disabled"]
}

function problemMessage({ configuration, provider }: TableSearchState) {
  if (configuration.error || provider.error)
    return "Could not load the search status."
  if (provider.isPending) return null
  if (provider.data?.state === "paused") return "Semantic search is paused."
  if (!provider.data?.available) return "No embedding provider is configured."
  return null
}

/** Show a table's semantic search status in the page header, with details on hover. */
export function TableSearchBadge() {
  const search = useTableSearchContext()
  if (!search?.canRead) return null
  const { configuration, provider, canUpdate } = search
  const label = tableSearchStatusLabel(search)
  const pending = PENDING_LABELS.has(label)
  const selected = (configuration.data?.selected_column_ids?.length ?? 0) > 0
  const index = configuration.data?.index
  const destination = provider.data?.configuration
  const problem = problemMessage(search)
  const Icon = pending ? Loader2 : TextSearchIcon
  return (
    <HoverCard openDelay={150}>
      <HoverCardTrigger asChild>
        <Badge
          variant="outline"
          tabIndex={0}
          aria-label={`Semantic search: ${label}`}
          className={cn(
            "h-7 cursor-default gap-1.5 whitespace-nowrap px-2 font-medium",
            TONE_CLASS_NAMES[label]
          )}
        >
          <Icon className={cn("size-3", pending && "animate-spin")} />
          {label}
        </Badge>
      </HoverCardTrigger>
      <HoverCardContent align="end" className="w-80 space-y-1 p-3 text-xs">
        <p className="font-medium">Semantic search</p>
        {destination && (
          <p className="text-muted-foreground">
            Model: {destination.provider} / {destination.model}
          </p>
        )}
        {selected && index && (
          <p className="text-muted-foreground">
            {index.ready ?? 0} ready · {index.pending ?? 0} pending ·{" "}
            {index.failed ?? 0} failed · {index.empty ?? 0} empty
          </p>
        )}
        {problem && (
          <p role="alert" className="text-muted-foreground">
            {problem}
          </p>
        )}
        {selected && canUpdate && (index?.failed ?? 0) > 0 && (
          <div className="pt-2 text-muted-foreground">
            <TableSearchProgress />
          </div>
        )}
      </HoverCardContent>
    </HoverCard>
  )
}
