"use client"

import { useState } from "react"
import type { TableSearchDisplayState } from "@/client"
import { useTableSearchContext } from "@/components/tables/table-search-context"
import { TableSearchProgress } from "@/components/tables/table-search-progress"
import { Button } from "@/components/ui/button"

const STATUS_LABELS: Record<TableSearchDisplayState, string> = {
  disabled: "Off",
  unavailable: "Unavailable",
  indexing: "Indexing",
  ready: "Ready",
  updating: "Updating",
  needs_attention: "Needs attention",
}

/** Show provider destination and truthful index readiness without a results UI. */
export function TableSearchStatus() {
  const search = useTableSearchContext()
  const [expanded, setExpanded] = useState(false)
  if (!search?.canRead) return null
  const { configuration, provider, refresh } = search
  const selected = (configuration.data?.selected_column_ids?.length ?? 0) > 0
  const index = configuration.data?.index
  const destination = provider.data?.configuration
  let label = STATUS_LABELS[configuration.data?.status ?? "disabled"]
  if (configuration.isPending || provider.isPending) label = "Checking status"
  else if (configuration.error || provider.error) label = "Needs attention"
  else if (!provider.data?.available || provider.data.state === "paused")
    label = "Unavailable"
  else if (selected && provider.data?.reindex_required)
    label = index?.backfill_complete ? "Updating" : "Indexing"
  return (
    <div className="border-b px-2 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <button
          type="button"
          className="font-medium underline-offset-4 hover:underline"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          Semantic search: {label}
        </button>
        {destination && (
          <span className="text-muted-foreground">
            {destination.provider} / {destination.model}
          </span>
        )}
        {selected && index && (
          <span className="text-muted-foreground">
            {index.ready} ready · {index.pending} pending · {index.failed}{" "}
            failed · {index.empty} empty
          </span>
        )}
        <Button
          variant="ghost"
          size="sm"
          className="ml-auto h-6 text-xs"
          disabled={configuration.isFetching || provider.isFetching}
          onClick={() => void refresh()}
        >
          Refresh search status
        </Button>
      </div>
      {expanded && (
        <div className="mt-2 space-y-2 text-muted-foreground">
          {destination && (
            <p>
              Selected text and search queries are sent to{" "}
              {destination.provider} using {destination.model}.
            </p>
          )}
          {!provider.isPending &&
            !provider.error &&
            !provider.data?.available && (
              <p>
                No eligible AI provider is configured. Ordinary table operations
                and literal text search remain available.
              </p>
            )}
          {provider.data?.state === "paused" && (
            <p>
              Semantic search is paused. Existing table controls remain
              available.
            </p>
          )}
          {(configuration.error || provider.error) && (
            <p role="alert">
              Could not load semantic search status. Refresh to try again; check
              your AI provider settings if the issue continues.
            </p>
          )}
          {provider.data?.reindex_required && selected && (
            <p>
              The embedding model or provider settings changed. Selected text
              will be indexed again before search is ready.
            </p>
          )}
          {!selected && (
            <p>
              Choose “Include in semantic search” from a TEXT column’s menu to
              start.
            </p>
          )}
          {selected && (
            <p>
              A row is ready only after all of its text has been indexed.
              Editing selected text makes that row unavailable until indexing
              finishes. Changing a selected column’s type or deleting it removes
              it from the selection and rebuilds the remaining index.
            </p>
          )}
          {selected && (configuration.data?.generation ?? 0) > 0 && (
            <TableSearchProgress />
          )}
        </div>
      )}
    </div>
  )
}
