"use client"

import Link from "next/link"
import type { TableColumnRead } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { useTableSearchContext } from "@/components/tables/table-search-context"
import {
  DropdownMenuCheckboxItem,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu"

/** Select TEXT columns independently of the table's unique index. */
export function TableSearchColumnControl({
  column,
}: {
  column: TableColumnRead
}) {
  const search = useTableSearchContext()
  const canViewProviders = useScopeCheck("org:settings:read") === true
  if (!search?.canRead) return null
  const { configuration, provider, selection, retry, canUpdate } = search
  const selected =
    configuration.data?.selected_column_ids?.includes(column.id) ?? false
  const destination = provider.data?.configuration
  const unavailable =
    !provider.data?.available || provider.data.state === "paused"
  const error = Boolean(provider.error || configuration.error)
  return (
    <>
      <DropdownMenuSeparator />
      <DropdownMenuCheckboxItem
        className="text-xs"
        checked={selected}
        disabled={
          !canUpdate ||
          column.type.toUpperCase() !== "TEXT" ||
          selection.isPending ||
          retry.isPending ||
          configuration.isFetching ||
          !configuration.data ||
          Boolean(configuration.error) ||
          ((unavailable || Boolean(provider.error)) && !selected)
        }
        onSelect={(event) => event.preventDefault()}
        onCheckedChange={(enabled) =>
          selection.mutate({ columnId: column.id, enabled })
        }
      >
        Include in semantic search
      </DropdownMenuCheckboxItem>
      <div className="max-w-64 px-2 py-1 text-xs text-muted-foreground">
        {column.type.toUpperCase() !== "TEXT" && (
          <p>Only TEXT columns can be searched by meaning.</p>
        )}
        {error && (
          <p>
            Provider or index status could not be loaded. Refresh the table
            status to try again.
          </p>
        )}
        {provider.isPending && <p>Checking provider availability…</p>}
        {!provider.isPending && !error && unavailable && (
          <p>
            Semantic search is unavailable. An eligible AI provider is required.
          </p>
        )}
        {!error && !unavailable && destination && (
          <p>
            Selected text and queries are sent to {destination.provider} /{" "}
            {destination.model}.
          </p>
        )}
        {selected && (
          <p>
            Removing this column rebuilds the index for the remaining selection.
          </p>
        )}
      </div>
      {canViewProviders && (
        <DropdownMenuItem asChild className="text-xs">
          <Link href="/organization/settings/agent">AI provider settings</Link>
        </DropdownMenuItem>
      )}
    </>
  )
}
