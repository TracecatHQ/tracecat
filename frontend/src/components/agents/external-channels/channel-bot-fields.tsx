"use client"

import { Copy, Download, type LucideIcon } from "lucide-react"
import type { ReactNode } from "react"
import type { ChannelBotAppManifest, ChannelBotProvider } from "@/client"
import {
  CHANNEL_BOT_PROVIDER_NAMES,
  copyOrDownloadAppManifest,
  isUnreachableEndpoint,
} from "@/components/agents/external-channels/channel-bot-utils"
import { CopyField } from "@/components/copy-field"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"

/** A setup state as an icon and a short label; red only when it needs action. */
export function StatusLabel({
  icon: Icon,
  destructive = false,
  warning = false,
  children,
}: {
  icon: LucideIcon
  destructive?: boolean
  /** Stale but not broken. */
  warning?: boolean
  children: ReactNode
}) {
  let tone = "text-muted-foreground"
  if (destructive) {
    tone = "text-destructive"
  } else if (warning) {
    tone = "text-amber-600 dark:text-amber-400"
  }
  return (
    <span
      className={cn("inline-flex shrink-0 items-center gap-1.5 text-xs", tone)}
    >
      <Icon className="size-3.5 shrink-0" aria-hidden />
      {children}
    </span>
  )
}

/** The URL a provider sends events to, warning when it cannot reach it. */
export function EndpointField({
  label,
  value,
  help,
  provider,
}: {
  label: string
  value: string
  help?: string
  /** Who calls the endpoint, as the warning names them. */
  provider: ChannelBotProvider
}) {
  return (
    <CopyField
      label={label}
      value={value}
      help={help}
      warning={
        isUnreachableEndpoint(value)
          ? `${CHANNEL_BOT_PROVIDER_NAMES[provider]} can't reach this address. Set the deployment's public API URL to a public https address.`
          : null
      }
    />
  )
}

/** Copies a text app manifest or downloads a base64 one; disabled until it loads. */
export function AppManifestButton({
  manifest,
}: {
  manifest: ChannelBotAppManifest | null
}) {
  const download = manifest?.encoding === "base64"
  const Icon = download ? Download : Copy
  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      onClick={() => manifest && copyOrDownloadAppManifest(manifest)}
      disabled={!manifest}
    >
      <Icon className="mr-2 size-4" />
      {download ? "Download app manifest" : "Copy app manifest"}
    </Button>
  )
}

/** A labelled credential input; secrets never autofill a saved password. */
export function CredentialField({
  id,
  label,
  value,
  onChange,
  disabled,
  placeholder,
  secret = false,
  help,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  disabled: boolean
  placeholder: string
  secret?: boolean
  help?: string
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id} className="text-xs">
        {label}
      </Label>
      <Input
        id={id}
        type={secret ? "password" : "text"}
        autoComplete={secret ? "new-password" : "off"}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        placeholder={placeholder}
        aria-describedby={help ? `${id}-help` : undefined}
      />
      {help ? (
        <p id={`${id}-help`} className="text-xs text-muted-foreground">
          {help}
        </p>
      ) : null}
    </div>
  )
}
