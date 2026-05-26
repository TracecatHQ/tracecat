"use client"

import type { CatalogIntegrationRead } from "@/client"
import { ProviderIcon } from "@/components/icons"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"

type CtaIntent = "connect" | "configure" | "open"

interface CatalogCardProps {
  integration: CatalogIntegrationRead
  ctaIntent?: CtaIntent
  isConnected?: boolean
  onSelect: () => void
}

const ctaLabels: Record<CtaIntent, string> = {
  connect: "Connect",
  configure: "Configure",
  open: "Open",
}

function catalogStatusLabel(
  integration: CatalogIntegrationRead,
  isConnected: boolean
): string | null {
  if (isConnected) return "Connected"
  if (integration.source === "workspace") return "Built by me"
  return null
}

export function CatalogCard({
  integration,
  ctaIntent = "open",
  isConnected = false,
  onSelect,
}: CatalogCardProps) {
  const statusLabel = catalogStatusLabel(integration, isConnected)

  return (
    <Card
      className="flex h-full min-h-[120px] cursor-pointer flex-col gap-2.5 border bg-card p-4 shadow-none transition-colors hover:border-foreground/30"
      onClick={onSelect}
    >
      <div className="flex items-start justify-between gap-3">
        <ProviderIcon
          providerId={integration.namespace}
          className="size-9 shrink-0"
        />
        <Button
          variant="outline"
          size="sm"
          className="h-8 px-3 text-xs font-medium"
          onClick={(event) => {
            event.stopPropagation()
            onSelect()
          }}
        >
          {ctaLabels[ctaIntent]}
        </Button>
      </div>

      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex min-w-0 items-baseline gap-2">
          <h3 className="truncate text-sm font-semibold leading-5 text-foreground">
            {integration.display_name}
          </h3>
          {statusLabel ? (
            <span className="shrink-0 text-xs text-muted-foreground">
              {statusLabel}
            </span>
          ) : null}
        </div>
        <p className="line-clamp-2 text-sm leading-5 text-muted-foreground">
          {integration.description ?? "No description"}
        </p>
      </div>
    </Card>
  )
}
