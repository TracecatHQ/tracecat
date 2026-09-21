"use client"

import { useState } from "react"
import { tablesGetTableSearchProgress } from "@/client"
import { useTableSearchContext } from "@/components/tables/table-search-context"
import { Button } from "@/components/ui/button"
import { searchPollInterval, tableSearchKey } from "@/hooks/use-table-search"
import { useQuery } from "@/lib/query"

function errorMessage(code: string) {
  switch (code) {
    case "CREDENTIAL_INVALID":
      return "Check the AI provider credentials, then retry."
    case "RATE_LIMITED":
      return "The provider is busy. Retry in a moment."
    case "TIMEOUT":
    case "UNAVAILABLE":
    case "PROVIDER_UNAVAILABLE":
      return "The provider could not complete indexing. Retry in a moment."
    case "CONFIGURATION_INVALID":
    case "NOT_CONFIGURED":
      return "Check the AI provider configuration before retrying."
    default:
      return "Indexing could not complete. Retry, or check the AI provider settings if it continues."
  }
}

/** Fetch one bounded progress page only while the user has opened details. */
export function TableSearchProgress() {
  const search = useTableSearchContext()
  const generation = search?.configuration.data?.generation ?? 0
  const [page, setPage] = useState<{ generation: number; cursor?: string }>({
    generation,
  })
  const cursor = page.generation === generation ? page.cursor : undefined
  const workspaceId = search?.workspaceId ?? ""
  const tableId = search?.tableId ?? ""
  const progress = useQuery({
    queryKey: [
      ...tableSearchKey(workspaceId, tableId),
      "progress",
      generation,
      cursor,
    ],
    queryFn: () =>
      tablesGetTableSearchProgress({
        workspaceId,
        tableId,
        generation,
        cursor,
        limit: 20,
      }),
    enabled: Boolean(search?.canRead && generation),
    retry: false,
    meta: { suppressErrorToast: true },
    refetchInterval: (query) =>
      query.state.error || search?.configuration.error || search?.provider.error
        ? false
        : searchPollInterval(search?.configuration.data, search?.provider.data),
  })
  if (!search) return null
  const failedIds =
    progress.data?.items
      .filter((item) => item.state === "failed")
      .map((item) => item.document_id) ?? []
  return (
    <div className="max-h-64 space-y-2 overflow-auto">
      <p className="font-medium text-foreground">
        Row progress · up to 20 rows per page
      </p>
      {progress.isPending && <p>Loading progress…</p>}
      {progress.error && (
        <p role="alert">
          Progress could not be loaded. Refresh search status to try again.
        </p>
      )}
      {progress.data?.items.map((item) => (
        <div key={item.document_id} className="border-t pt-2">
          <p className="truncate">
            Row {item.row_id}: {item.state}
          </p>
          <p>
            {item.sampled_embedded} embedded in a sample of{" "}
            {item.sampled_chunks} chunks
            {item.chunks_capped ? " (more chunks exist)" : ""}.{" "}
            {item.expected_chunks === null
              ? "The total is still being discovered."
              : `${item.expected_chunks} chunks total.`}
          </p>
          {item.error_code && (
            <p role="alert">{errorMessage(item.error_code)}</p>
          )}
        </div>
      ))}
      {progress.data?.items.length === 0 && <p>No row progress to show yet.</p>}
      <div className="flex flex-wrap gap-2">
        {search.canUpdate && failedIds.length > 0 && (
          <Button
            size="sm"
            variant="outline"
            disabled={
              search.provider.data?.state !== "active" ||
              search.configuration.data?.index?.state !== "active" ||
              search.retry.isPending ||
              search.selection.isPending ||
              progress.isFetching
            }
            onClick={() => search.retry.mutate(failedIds)}
          >
            Retry failed rows on this page
          </Button>
        )}
        {cursor && (
          <Button
            size="sm"
            variant="ghost"
            disabled={progress.isFetching}
            onClick={() => setPage({ generation })}
          >
            First page
          </Button>
        )}
        {progress.data?.has_more && progress.data.next_cursor && (
          <Button
            size="sm"
            variant="ghost"
            disabled={progress.isFetching}
            onClick={() =>
              setPage({
                generation,
                cursor: progress.data?.next_cursor ?? undefined,
              })
            }
          >
            Next progress page
          </Button>
        )}
      </div>
    </div>
  )
}
