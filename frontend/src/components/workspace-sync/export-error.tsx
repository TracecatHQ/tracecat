import Link from "next/link"
import type { WorkspaceSyncExportError } from "@/client"
import { getApiErrorDetail } from "@/lib/errors"

/** Read typed export errors while accepting older string-only responses. */
export function getExportError(
  error: unknown
): WorkspaceSyncExportError | null {
  if (!(error instanceof Error) || !("body" in error)) return null
  const body = error.body
  if (typeof body !== "object" || body === null || !("diagnostics" in body)) {
    return null
  }
  if (!Array.isArray(body.diagnostics)) return null
  const diagnostics = body.diagnostics.filter(
    (
      item
    ): item is NonNullable<WorkspaceSyncExportError["diagnostics"]>[number] =>
      typeof item === "object" &&
      item !== null &&
      typeof item.workflow_id === "string" &&
      /^[0-9a-f-]{36}$/i.test(item.workflow_id) &&
      typeof item.workflow_title === "string" &&
      typeof item.message === "string"
  )
  if (diagnostics.length === 0) return null
  return {
    detail: getApiErrorDetail(error) ?? "Push failed",
    diagnostics,
    omitted_count:
      "omitted_count" in body && typeof body.omitted_count === "number"
        ? body.omitted_count
        : 0,
  }
}

/** Show named corrective errors with local, workspace-scoped editor links. */
export function ExportErrorMessage({
  error,
  workspaceId,
  announce = true,
}: {
  error: unknown
  workspaceId: string
  announce?: boolean
}) {
  const result = getExportError(error)
  if (!result) return <>{getApiErrorDetail(error) ?? "Request failed"}</>
  return (
    <div className="space-y-1" role={announce ? "alert" : undefined}>
      <ul className="space-y-1">
        {result.diagnostics?.map((item, index) => (
          <li key={`${item.workflow_id}:${index}`}>
            <Link
              className="underline"
              href={`/workspaces/${encodeURIComponent(workspaceId)}/workflows/${item.workflow_id}`}
            >
              {item.workflow_title}
            </Link>
            : {item.message}
          </li>
        ))}
      </ul>
      {!!result.omitted_count && (
        <p>{result.omitted_count} additional error(s) omitted.</p>
      )}
    </div>
  )
}
