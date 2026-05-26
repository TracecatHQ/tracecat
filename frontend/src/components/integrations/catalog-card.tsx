"use client"

import { AlertTriangle, KeyRound, Lock, Plug } from "lucide-react"
import type { CatalogAuthOption, CatalogIntegrationRead } from "@/client"
import { ProviderIcon } from "@/components/icons"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { cn } from "@/lib/utils"

type CtaIntent = "connect" | "configure" | "open"

interface CatalogCardProps {
  integration: CatalogIntegrationRead
  ctaIntent?: CtaIntent
  ctaSublabel?: string | null
  isConnected?: boolean
  onSelect: () => void
}

const ctaLabels: Record<CtaIntent, string> = {
  connect: "Connect",
  configure: "Configure",
  open: "Open",
}

function optionKey(option: CatalogAuthOption): string {
  return [
    option.auth_method,
    option.provider_id ?? "static",
    option.grant_type ?? "none",
  ].join(":")
}

function authOptionLabel(option: CatalogAuthOption): string {
  if (option.auth_method === "static_kv" && option.fields?.length === 1) {
    return "API key"
  }
  return option.label
}

function AuthOptionBadge({ option }: { option: CatalogAuthOption }) {
  const needsConfiguration =
    option.requires_config === true && option.status === "not_configured"
  const isOAuth = option.auth_method.startsWith("oauth")

  return (
    <Badge
      variant="outline"
      className={cn(
        "h-5 gap-1 px-1.5 text-[10px] font-medium",
        needsConfiguration && "border-amber-400/60 bg-amber-50 text-amber-700"
      )}
    >
      {isOAuth ? <Lock className="size-3" /> : <KeyRound className="size-3" />}
      {authOptionLabel(option)}
    </Badge>
  )
}

export function CatalogCard({
  integration,
  ctaIntent = "open",
  ctaSublabel,
  isConnected = false,
  onSelect,
}: CatalogCardProps) {
  const requiresConfig = ctaIntent === "configure"
  const authOptions = (integration.auth_options ?? []).filter(
    (option) => option.enabled !== false
  )

  return (
    <Card
      className={cn(
        "flex h-full flex-col gap-3 border bg-card p-4 shadow-none transition-colors hover:border-foreground/30 cursor-pointer"
      )}
      onClick={onSelect}
    >
      <div className="flex items-start gap-3">
        <ProviderIcon
          providerId={integration.namespace}
          className="size-10 shrink-0"
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-sm font-semibold text-foreground">
              {integration.display_name}
            </h3>
            {integration.source === "workspace" ? (
              <Badge
                variant="secondary"
                className="h-4 shrink-0 px-1.5 text-[10px] font-medium"
              >
                Built by me
              </Badge>
            ) : null}
          </div>
          <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
            {integration.description ?? "No description"}
          </p>
        </div>
      </div>

      {authOptions.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {authOptions.map((option) => (
            <AuthOptionBadge key={optionKey(option)} option={option} />
          ))}
        </div>
      ) : null}

      <div className="mt-auto flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5">
          {isConnected ? (
            <Badge
              variant="outline"
              className="h-5 gap-1 border-emerald-400/50 bg-emerald-500/10 px-1.5 text-[10px] font-medium text-emerald-700"
            >
              <Plug className="size-3" />
              Connected
            </Badge>
          ) : null}
          {requiresConfig ? (
            <span className="flex items-center gap-1 text-[11px] font-medium text-amber-600">
              <AlertTriangle className="size-3" />
              {ctaSublabel ?? "Configuration missing"}
            </span>
          ) : null}
        </div>
        <Button
          variant={requiresConfig ? "default" : "outline"}
          size="sm"
          className={cn(
            "h-7 px-2.5 text-xs",
            requiresConfig &&
              "border-amber-500 bg-amber-500/90 text-white hover:bg-amber-500"
          )}
          onClick={(event) => {
            event.stopPropagation()
            onSelect()
          }}
        >
          {ctaLabels[ctaIntent]}
        </Button>
      </div>
    </Card>
  )
}
