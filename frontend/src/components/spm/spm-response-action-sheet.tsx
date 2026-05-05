"use client"

import { BoltIcon } from "lucide-react"
import type { SpmResponseActionRead } from "@/client"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { formatLabel } from "./spm-common"
import { itemTypeIcon, itemTypeLabel } from "./spm-icons"
import { SmallBadge } from "./spm-layout"

export function SpmResponseActionSheet(props: {
  action: SpmResponseActionRead | null
  onOpenChange: (open: boolean) => void
  open: boolean
}) {
  const action = props.action
  const payloadFields = action?.payload_fields ?? []
  return (
    <Sheet open={props.open} onOpenChange={props.onOpenChange}>
      <SheetContent side="right" className="w-3/4 p-0 sm:max-w-xl">
        {action ? (
          <div className="flex h-full flex-col">
            <SheetHeader className="border-b px-5 py-4 pr-12">
              <div className="flex min-w-0 items-start gap-3">
                <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md border bg-muted/40">
                  <BoltIcon className="size-4 text-muted-foreground" />
                </div>
                <div className="min-w-0 flex-1">
                  <SheetTitle className="truncate text-base">
                    {action.title}
                  </SheetTitle>
                  <SheetDescription className="mt-1 flex flex-wrap items-center gap-2 text-xs">
                    <span className="font-mono">{action.key}</span>
                    <span>{formatLabel(action.execution_mode)}</span>
                  </SheetDescription>
                </div>
              </div>
            </SheetHeader>

            <div className="min-h-0 flex-1 overflow-auto px-5 py-4">
              <section className="space-y-2">
                <h3 className="text-xs font-medium text-muted-foreground">
                  Description
                </h3>
                <p className="text-sm leading-6">{action.description}</p>
              </section>

              <section className="mt-6 space-y-3">
                <h3 className="text-xs font-medium text-muted-foreground">
                  Targets
                </h3>
                <div className="flex flex-wrap gap-2">
                  {action.item_types.map((itemType) => {
                    const ItemIcon = itemTypeIcon(itemType)
                    return (
                      <SmallBadge key={itemType} icon={ItemIcon}>
                        {itemTypeLabel(itemType)}
                      </SmallBadge>
                    )
                  })}
                  <SmallBadge>{action.target_surface}</SmallBadge>
                  {action.preview_supported ? (
                    <SmallBadge>Preview</SmallBadge>
                  ) : null}
                  {action.disruptive ? (
                    <SmallBadge>Disruptive</SmallBadge>
                  ) : null}
                </div>
              </section>

              <section className="mt-6 space-y-3">
                <h3 className="text-xs font-medium text-muted-foreground">
                  Payload fields
                </h3>
                <div className="flex flex-wrap gap-2">
                  {payloadFields.length === 0 ? (
                    <SmallBadge>No payload fields</SmallBadge>
                  ) : (
                    payloadFields.map((field) => (
                      <SmallBadge key={field}>{field}</SmallBadge>
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
