"use client"

import {
  ChevronLeftIcon,
  ChevronRightIcon,
  DownloadIcon,
  EyeIcon,
  LoaderIcon,
  XIcon,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import {
  type CollectionObject,
  type WorkflowExecutionCollectionPageItem,
  type WorkflowExecutionCollectionPageResponse,
  type WorkflowExecutionObjectPreviewResponse,
  workflowExecutionsGetWorkflowExecutionCollectionPage,
  workflowExecutionsGetWorkflowExecutionObjectDownload,
  workflowExecutionsGetWorkflowExecutionObjectPreview,
} from "@/client"
import { CodeBlock } from "@/components/code-block"
import { ExternalObjectResult } from "@/components/executions/external-object-result"
import { JsonViewWithControls } from "@/components/json-viewer"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { toast } from "@/components/ui/use-toast"
import {
  isExternalStoredObject,
  isInlineStoredObject,
} from "@/lib/stored-object"
import { useWorkspaceId } from "@/providers/workspace-id"

const DEFAULT_PAGE_SIZE = 25

function getElementKindLabel(kind: CollectionObject["element_kind"]): string {
  if (kind === "stored_object") {
    return "Stored items"
  }
  return "Inline values"
}

function getItemKindLabel(item: WorkflowExecutionCollectionPageItem): string {
  if (item.kind === "inline_value") {
    return "Value"
  }
  if (item.stored && isExternalStoredObject(item.stored)) {
    return "File"
  }
  if (item.stored && isInlineStoredObject(item.stored)) {
    return "Value"
  }
  return "Stored item"
}

function formatSize(sizeBytes: number): string {
  if (sizeBytes === 0) {
    return "0 Bytes"
  }
  const units = ["Bytes", "KB", "MB", "GB", "TB"]
  const exponent = Math.min(
    Math.floor(Math.log(sizeBytes) / Math.log(1024)),
    units.length - 1
  )
  const value = sizeBytes / 1024 ** exponent
  return `${value.toFixed(exponent === 0 ? 0 : 1)} ${units[exponent]}`
}

function formatPageWindow(
  offset: number,
  count: number,
  total: number
): string {
  if (count === 0) {
    return "0 items"
  }
  const start = offset + 1
  const end = Math.min(offset + count, total)
  return `${start}–${end} of ${total}`
}

function shouldProbeDownloadUrl(downloadUrl: string): boolean {
  try {
    const parsed = new URL(downloadUrl, window.location.href)
    return parsed.origin === window.location.origin
  } catch {
    return false
  }
}

export function CollectionObjectResult({
  executionId,
  eventId,
  collection,
}: {
  executionId: string
  eventId: number
  collection: CollectionObject
}) {
  const workspaceId = useWorkspaceId()
  const [offset, setOffset] = useState(0)
  const [isLoadingPage, setIsLoadingPage] = useState(false)
  const [pageError, setPageError] = useState<string | null>(null)
  const [page, setPage] =
    useState<WorkflowExecutionCollectionPageResponse | null>(null)
  const [isPreviewingIndex, setIsPreviewingIndex] = useState<number | null>(
    null
  )
  const [isDownloadingIndex, setIsDownloadingIndex] = useState<number | null>(
    null
  )
  const [previewByIndex, setPreviewByIndex] = useState<
    Record<number, WorkflowExecutionObjectPreviewResponse | undefined>
  >({})

  useEffect(() => {
    setOffset(0)
    setPreviewByIndex({})
  }, [eventId, executionId])

  useEffect(() => {
    let cancelled = false
    const loadPage = async () => {
      setIsLoadingPage(true)
      setPageError(null)
      try {
        const response =
          await workflowExecutionsGetWorkflowExecutionCollectionPage({
            executionId,
            workspaceId,
            requestBody: {
              event_id: eventId,
              offset,
              limit: DEFAULT_PAGE_SIZE,
            },
          })
        if (!cancelled) {
          setPage(response)
        }
      } catch (error) {
        if (!cancelled) {
          const message =
            error instanceof Error
              ? error.message
              : "Failed to fetch collection page"
          setPageError(message)
        }
      } finally {
        if (!cancelled) {
          setIsLoadingPage(false)
        }
      }
    }
    void loadPage()
    return () => {
      cancelled = true
    }
  }, [eventId, executionId, offset, workspaceId])

  const resolvedCollection = page?.collection ?? collection
  const pageItems = page?.items ?? []
  const canPrev = offset > 0
  const canNext = (page?.next_offset ?? null) !== null
  const summaryLabel = useMemo(
    () =>
      `${resolvedCollection.count} items • page size ${resolvedCollection.chunk_size} • ${getElementKindLabel(resolvedCollection.element_kind)}`,
    [
      resolvedCollection.chunk_size,
      resolvedCollection.count,
      resolvedCollection.element_kind,
    ]
  )
  const pageWindow = formatPageWindow(
    offset,
    pageItems.length,
    resolvedCollection.count
  )

  function closeInlinePreview(index: number) {
    setPreviewByIndex((curr) => {
      if (curr[index] === undefined) {
        return curr
      }
      const next = { ...curr }
      delete next[index]
      return next
    })
  }

  async function handleInlinePreview(index: number) {
    setIsPreviewingIndex(index)
    try {
      const response =
        await workflowExecutionsGetWorkflowExecutionObjectPreview({
          executionId,
          workspaceId,
          requestBody: {
            event_id: eventId,
            collection_index: index,
          },
        })
      setPreviewByIndex((curr) => ({ ...curr, [index]: response }))
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Failed to preview item"
      toast({
        title: "Preview failed",
        description: message,
      })
    } finally {
      setIsPreviewingIndex(null)
    }
  }

  async function handleInlineDownload(index: number) {
    setIsDownloadingIndex(index)
    try {
      const response =
        await workflowExecutionsGetWorkflowExecutionObjectDownload({
          executionId,
          workspaceId,
          requestBody: {
            event_id: eventId,
            collection_index: index,
          },
        })

      if (shouldProbeDownloadUrl(response.download_url)) {
        const probe = await fetch(response.download_url, {
          method: "GET",
          headers: {
            Range: "bytes=0-0",
          },
        })
        if (!probe.ok) {
          throw new Error(
            `Object download probe failed (${probe.status} ${probe.statusText})`
          )
        }
      }

      const link = document.createElement("a")
      link.href = response.download_url
      link.download = response.file_name
      link.rel = "noopener"
      link.style.display = "none"
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Failed to download item"
      toast({
        title: "Download failed",
        description: message,
      })
    } finally {
      setIsDownloadingIndex(null)
    }
  }

  return (
    <div className="flex flex-col gap-3 p-3 text-xs">
      <div className="flex items-center justify-between gap-2">
        <div className="space-y-0.5">
          <p className="text-sm font-medium text-foreground">
            Collection results
          </p>
          <p className="text-muted-foreground">{summaryLabel}</p>
        </div>
        <Badge variant="secondary" className="text-[10px]">
          {pageWindow}
        </Badge>
      </div>

      {isLoadingPage && (
        <div className="flex items-center gap-2 text-muted-foreground">
          <LoaderIcon className="size-3 animate-spin" />
          <span>Loading collection page...</span>
        </div>
      )}

      {pageError && (
        <div className="text-rose-600">
          Failed to load collection page: {pageError}
        </div>
      )}

      {!isLoadingPage && !pageError && (
        <div className="overflow-hidden rounded-md border bg-background">
          {pageItems.length === 0 ? (
            <div className="px-3 py-2 text-muted-foreground">
              This page has no items.
            </div>
          ) : (
            pageItems.map((item) => (
              <div
                key={item.index}
                className="flex flex-col gap-2 border-b px-3 py-2.5 last:border-b-0"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-foreground">
                      Item {item.index}
                    </span>
                    <span className="text-muted-foreground">•</span>
                    <span className="text-muted-foreground">
                      {getItemKindLabel(item)}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 text-muted-foreground">
                    {item.value_size_bytes !== null &&
                    item.value_size_bytes !== undefined ? (
                      <span>{formatSize(item.value_size_bytes)}</span>
                    ) : null}
                    {item.truncated ? (
                      <>
                        <span>•</span>
                        <span>Preview truncated</span>
                      </>
                    ) : null}
                  </div>
                </div>

                {item.kind === "stored_object_ref" && item.stored ? (
                  isExternalStoredObject(item.stored) ? (
                    <ExternalObjectResult
                      executionId={executionId}
                      eventId={eventId}
                      external={item.stored}
                      collectionIndex={item.index}
                      compact={true}
                    />
                  ) : isInlineStoredObject(item.stored) ? (
                    <div className="space-y-2">
                      <CodeBlock title={`Item ${item.index} value`}>
                        {JSON.stringify(item.stored.data, null, 2)}
                      </CodeBlock>
                      <div className="flex items-center gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-7 text-xs"
                          onClick={() => void handleInlinePreview(item.index)}
                          disabled={isPreviewingIndex === item.index}
                        >
                          {isPreviewingIndex === item.index ? (
                            <LoaderIcon className="mr-2 size-3 animate-spin" />
                          ) : (
                            <EyeIcon className="mr-2 size-3" />
                          )}
                          Preview
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-7 text-xs"
                          onClick={() => void handleInlineDownload(item.index)}
                          disabled={isDownloadingIndex === item.index}
                        >
                          {isDownloadingIndex === item.index ? (
                            <LoaderIcon className="mr-2 size-3 animate-spin" />
                          ) : (
                            <DownloadIcon className="mr-2 size-3" />
                          )}
                          Download
                        </Button>
                        {previewByIndex[item.index] ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 px-2 text-xs"
                            onClick={() => closeInlinePreview(item.index)}
                          >
                            <XIcon className="mr-1 size-3" />
                            Close preview
                          </Button>
                        ) : null}
                      </div>
                      {previewByIndex[item.index] ? (
                        <CodeBlock title={`Preview item ${item.index}`}>
                          {previewByIndex[item.index]?.content ?? ""}
                        </CodeBlock>
                      ) : null}
                    </div>
                  ) : (
                    <div className="rounded-sm border border-dashed p-2">
                      <JsonViewWithControls
                        src={item.stored}
                        defaultExpanded={false}
                      />
                    </div>
                  )
                ) : (
                  <div className="space-y-2">
                    {item.value_preview ? (
                      <CodeBlock title={`Item ${item.index} preview`}>
                        {item.value_preview}
                      </CodeBlock>
                    ) : (
                      <div className="text-muted-foreground">
                        No inline preview available.
                      </div>
                    )}
                    <div className="flex items-center gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-7 text-xs"
                        onClick={() => void handleInlinePreview(item.index)}
                        disabled={isPreviewingIndex === item.index}
                      >
                        {isPreviewingIndex === item.index ? (
                          <LoaderIcon className="mr-2 size-3 animate-spin" />
                        ) : (
                          <EyeIcon className="mr-2 size-3" />
                        )}
                        Preview
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-7 text-xs"
                        onClick={() => void handleInlineDownload(item.index)}
                        disabled={isDownloadingIndex === item.index}
                      >
                        {isDownloadingIndex === item.index ? (
                          <LoaderIcon className="mr-2 size-3 animate-spin" />
                        ) : (
                          <DownloadIcon className="mr-2 size-3" />
                        )}
                        Download
                      </Button>
                      {previewByIndex[item.index] ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 px-2 text-xs"
                          onClick={() => closeInlinePreview(item.index)}
                        >
                          <XIcon className="mr-1 size-3" />
                          Close preview
                        </Button>
                      ) : null}
                    </div>
                    {previewByIndex[item.index] ? (
                      <CodeBlock title={`Preview item ${item.index}`}>
                        {previewByIndex[item.index]?.content ?? ""}
                      </CodeBlock>
                    ) : null}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      )}

      <div className="flex items-center justify-between gap-2">
        <span className="text-muted-foreground">{pageWindow}</span>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            disabled={!canPrev || isLoadingPage}
            onClick={() =>
              setOffset((prev) => Math.max(0, prev - DEFAULT_PAGE_SIZE))
            }
          >
            <ChevronLeftIcon className="mr-2 size-3" />
            Prev
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            disabled={!canNext || isLoadingPage}
            onClick={() => {
              if (
                page?.next_offset !== null &&
                page?.next_offset !== undefined
              ) {
                setOffset(page.next_offset)
              }
            }}
          >
            Next
            <ChevronRightIcon className="ml-2 size-3" />
          </Button>
        </div>
      </div>
    </div>
  )
}
