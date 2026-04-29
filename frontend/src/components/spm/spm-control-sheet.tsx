"use client"

import type { SpmControlRead, SpmFindingRead } from "@/client"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { useSpmEndpoints, useSpmFindings } from "@/hooks/use-spm"
import { cn } from "@/lib/utils"
import { formatLabel, getEndpointName } from "./spm-common"
import { assetTypeIcon, assetTypeLabel } from "./spm-icons"
import { SmallBadge } from "./spm-layout"

function groupFindingsByEndpoint(findings: SpmFindingRead[]) {
  const grouped = new Map<string, SpmFindingRead[]>()
  for (const finding of findings) {
    const next = grouped.get(finding.endpoint_id) ?? []
    next.push(finding)
    grouped.set(finding.endpoint_id, next)
  }
  return Array.from(grouped.entries())
}

export function SpmControlSheet(props: {
  control: SpmControlRead | null
  onOpenChange: (open: boolean) => void
  open: boolean
}) {
  const findingsQuery = useSpmFindings({
    controlId: props.control?.id,
    enabled: props.open && props.control != null,
    limit: 100,
  })
  const endpointsQuery = useSpmEndpoints()
  const findings = (findingsQuery.data?.items ?? []).filter(
    (finding) => finding.status === "open"
  )
  const endpoints = endpointsQuery.data?.items ?? []
  const groupedFindings = groupFindingsByEndpoint(findings)
  const control = props.control
  const AssetIcon = control ? assetTypeIcon(control.asset_type) : null

  return (
    <Sheet open={props.open} onOpenChange={props.onOpenChange}>
      <SheetContent side="right" className="w-3/4 p-0 sm:max-w-xl">
        {control ? (
          <div className="flex h-full flex-col">
            <SheetHeader className="border-b px-5 py-4 pr-12">
              <div className="flex min-w-0 items-start gap-3">
                {AssetIcon ? (
                  <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md border bg-muted/40">
                    <AssetIcon className="size-4 text-muted-foreground" />
                  </div>
                ) : null}
                <div className="min-w-0 flex-1">
                  <SheetTitle className="truncate text-base">
                    {control.title}
                  </SheetTitle>
                  <SheetDescription className="mt-1 flex flex-wrap items-center gap-2 text-xs">
                    <span className="font-mono">{control.key}</span>
                    <span>Response: {formatLabel(control.action)}</span>
                  </SheetDescription>
                </div>
              </div>
            </SheetHeader>

            <div className="min-h-0 flex-1 overflow-auto px-5 py-4">
              <section className="space-y-2">
                <h3 className="text-xs font-medium text-muted-foreground">
                  Description
                </h3>
                <p className="text-sm leading-6">{control.description}</p>
              </section>

              <section className="mt-6 space-y-3">
                <div className="flex items-center justify-between">
                  <h3 className="text-xs font-medium text-muted-foreground">
                    Open findings
                  </h3>
                  <SmallBadge>{findings.length}</SmallBadge>
                </div>
                <div className="divide-y rounded-md border">
                  {groupedFindings.length === 0 ? (
                    <div className="px-3 py-6 text-center text-xs text-muted-foreground">
                      No open findings
                    </div>
                  ) : (
                    groupedFindings.map(([endpointId, endpointFindings]) => (
                      <div key={endpointId} className="px-3 py-3">
                        <div className="mb-2 flex items-center justify-between gap-3">
                          <span className="truncate text-xs font-medium">
                            {getEndpointName(endpointId, endpoints)}
                          </span>
                          <SmallBadge>
                            {endpointFindings.length} findings
                          </SmallBadge>
                        </div>
                        <div className="space-y-1.5">
                          {endpointFindings.map((finding) => (
                            <div
                              key={finding.id}
                              className="flex min-w-0 items-center gap-2 text-xs"
                            >
                              <span
                                className={cn(
                                  "h-1.5 w-1.5 shrink-0 rounded-full",
                                  finding.severity === "critical"
                                    ? "bg-fuchsia-600"
                                    : finding.severity === "high"
                                      ? "bg-red-600"
                                      : finding.severity === "medium"
                                        ? "bg-orange-600"
                                        : "bg-yellow-600"
                                )}
                              />
                              <span className="truncate">
                                {finding.summary}
                              </span>
                              <SmallBadge>
                                {assetTypeLabel(finding.asset_type)}
                              </SmallBadge>
                            </div>
                          ))}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </section>
            </div>
          </div>
        ) : null}
      </SheetContent>
    </Sheet>
  )
}
