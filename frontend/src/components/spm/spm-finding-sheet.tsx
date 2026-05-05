"use client"

import { ShieldAlertIcon, WandSparklesIcon } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import type {
  SpmEndpointRead,
  SpmFindingRead,
  SpmInventoryItemRead,
  SpmResponseActionRead,
} from "@/client"
import { DiffViewer } from "@/components/common/diff-viewer"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { useToast } from "@/components/ui/use-toast"
import { useSpmActions, useSpmResponseActionPreview } from "@/hooks/use-spm"
import { getApiErrorDetail } from "@/lib/errors"
import {
  formatLabel,
  getEndpointName,
  getInventoryItemPath,
  getInventoryItemRecord,
} from "./spm-common"
import { itemTypeIcon, itemTypeLabel, sourceTypeLabel } from "./spm-icons"
import { SmallBadge } from "./spm-layout"

export function SpmFindingSheet(props: {
  actions: SpmResponseActionRead[]
  endpoints: SpmEndpointRead[]
  finding: SpmFindingRead | null
  inventoryItems: SpmInventoryItemRead[]
  onOpenChange: (open: boolean) => void
  open: boolean
}) {
  const { toast } = useToast()
  const { createResponseActionPreview, decideFinding } = useSpmActions()
  const [previewId, setPreviewId] = useState<string | undefined>(undefined)
  const requestedFindingId = useRef<string | null>(null)
  const finding = props.finding
  const action = finding?.recommended_action
    ? props.actions.find((item) => item.key === finding.recommended_action)
    : null
  const previewQuery = useSpmResponseActionPreview({
    enabled: props.open && previewId != null,
    previewId,
  })
  const preview = previewQuery.data

  useEffect(() => {
    if (!props.open || !finding?.recommended_action) return
    if (requestedFindingId.current === finding.id) return

    requestedFindingId.current = finding.id
    setPreviewId(undefined)
    createResponseActionPreview
      .mutateAsync({
        findingId: finding.id,
        requestBody: {},
      })
      .then((created) => setPreviewId(created.id))
      .catch((error) => {
        toast({
          title: "Preview failed",
          description:
            getApiErrorDetail(error) ?? "Unable to request action preview.",
          variant: "destructive",
        })
      })
  }, [createResponseActionPreview, finding, props.open, toast])

  async function handleEnforce() {
    if (!finding?.recommended_action) return
    try {
      await decideFinding.mutateAsync({
        findingId: finding.id,
        requestBody: {
          decision: "enforce",
        },
      })
      toast({
        title: "Enforcement queued",
        description: "The endpoint will apply this response action on sync.",
      })
      props.onOpenChange(false)
    } catch (error) {
      toast({
        title: "Enforcement failed",
        description:
          getApiErrorDetail(error) ?? "Unable to queue response action.",
        variant: "destructive",
      })
    }
  }

  const item = finding
    ? getInventoryItemRecord(finding.inventory_item_id, props.inventoryItems)
    : null
  const ItemIcon = finding ? itemTypeIcon(finding.item_type) : ShieldAlertIcon

  return (
    <Sheet open={props.open} onOpenChange={props.onOpenChange}>
      <SheetContent side="right" className="w-3/4 p-0 sm:max-w-3xl">
        {finding ? (
          <div className="flex h-full flex-col">
            <SheetHeader className="border-b px-5 py-4 pr-12">
              <div className="flex min-w-0 items-start gap-3">
                <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md border bg-muted/40">
                  <ItemIcon className="size-4 text-muted-foreground" />
                </div>
                <div className="min-w-0 flex-1">
                  <SheetTitle className="truncate text-base">
                    {finding.summary}
                  </SheetTitle>
                  <SheetDescription className="mt-1 flex flex-wrap items-center gap-2 text-xs">
                    <span>
                      {getEndpointName(finding.endpoint_id, props.endpoints)}
                    </span>
                    <span>{formatLabel(finding.severity)}</span>
                    <span>{formatLabel(finding.status)}</span>
                  </SheetDescription>
                </div>
              </div>
            </SheetHeader>

            <div className="min-h-0 flex-1 space-y-6 overflow-auto px-5 py-4">
              <section className="space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                  <SmallBadge icon={ItemIcon}>
                    {itemTypeLabel(finding.item_type)}
                  </SmallBadge>
                  <SmallBadge>
                    {sourceTypeLabel(finding.source_type)}
                  </SmallBadge>
                  <SmallBadge>
                    {item
                      ? getInventoryItemPath(item)
                      : finding.source_location}
                  </SmallBadge>
                </div>
                <div className="rounded-md border bg-muted/20 px-3 py-2 text-xs">
                  <div className="font-medium">{finding.control_key}</div>
                  <div className="mt-1 text-muted-foreground">
                    Source: {finding.source_location}
                  </div>
                </div>
              </section>

              <section className="space-y-3">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h3 className="text-xs font-medium text-muted-foreground">
                      Response action
                    </h3>
                    <p className="mt-1 text-sm">
                      {action?.title ??
                        (finding.recommended_action
                          ? formatLabel(finding.recommended_action)
                          : "No response action")}
                    </p>
                  </div>
                  <Button
                    size="sm"
                    className="h-8 gap-1.5"
                    disabled={
                      !finding.recommended_action || decideFinding.isPending
                    }
                    onClick={() => void handleEnforce()}
                  >
                    <WandSparklesIcon className="size-3.5" />
                    {decideFinding.isPending ? "Queueing..." : "Enforce"}
                  </Button>
                </div>
                {action ? (
                  <p className="text-sm leading-6 text-muted-foreground">
                    {action.description}
                  </p>
                ) : (
                  <Alert>
                    <ShieldAlertIcon className="size-4" />
                    <AlertDescription className="text-xs">
                      This finding does not have a previewable response action.
                    </AlertDescription>
                  </Alert>
                )}
              </section>

              {finding.recommended_action ? (
                <section className="space-y-3">
                  <div className="flex items-center justify-between gap-3">
                    <h3 className="text-xs font-medium text-muted-foreground">
                      Preview diff
                    </h3>
                    {preview?.target_path ? (
                      <span className="truncate font-mono text-xs text-muted-foreground">
                        {preview.target_path}
                      </span>
                    ) : null}
                  </div>
                  {preview?.status === "ready" ? (
                    <DiffViewer
                      baseLabel="Current"
                      baseValue={preview.before_content}
                      compareLabel="After response action"
                      compareValue={preview.after_content}
                      emptyMessage="No file content returned for this preview."
                    />
                  ) : preview?.status === "failed" ? (
                    <Alert variant="destructive">
                      <ShieldAlertIcon className="size-4" />
                      <AlertDescription className="text-xs">
                        {preview.error ??
                          "Endpoint could not generate a preview."}
                      </AlertDescription>
                    </Alert>
                  ) : (
                    <div className="rounded-md border px-3 py-6 text-xs text-muted-foreground">
                      Waiting for endpoint preview...
                    </div>
                  )}
                </section>
              ) : null}
            </div>
          </div>
        ) : null}
      </SheetContent>
    </Sheet>
  )
}
