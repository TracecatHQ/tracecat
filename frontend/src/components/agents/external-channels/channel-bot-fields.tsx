"use client"

import { Copy, Download, type LucideIcon } from "lucide-react"
import type { ReactNode } from "react"
import type {
  AgentPresetReadMinimal,
  ChannelBotAppManifest,
  ChannelBotProvider,
} from "@/client"
import {
  CHANNEL_BOT_PROVIDER_NAMES,
  copyOrDownloadAppManifest,
  isUnreachableEndpoint,
} from "@/components/agents/external-channels/channel-bot-utils"
import { CopyField } from "@/components/copy-field"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"

/** Red only when someone must act. */
const DOT_TONES = {
  ok: "bg-green-600",
  attention: "bg-amber-600",
  action: "bg-destructive",
  neutral: "bg-zinc-400",
} as const

/** How a status dot reads: running, worth a look, must act, or nothing to report. */
export type StatusTone = keyof typeof DOT_TONES

/** A status as a small coloured dot before neutral text. */
export function StatusDot({
  tone,
  children,
}: {
  tone: StatusTone
  children: ReactNode
}) {
  return (
    <span className="inline-flex min-w-0 max-w-full items-center gap-1.5 text-xs text-foreground/80">
      <span
        aria-hidden
        className={cn("size-1.5 shrink-0 rounded-full", DOT_TONES[tone])}
      />
      {children}
    </span>
  )
}

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

/** An agent a picker can offer. */
export type AgentOption = Pick<AgentPresetReadMinimal, "id" | "name">

/** `agents`, with the current agent first when the list leaves it out. */
export function withCurrentAgent(
  agents: AgentOption[],
  current: AgentOption | undefined
): AgentOption[] {
  if (!current || agents.some((agent) => agent.id === current.id)) {
    return agents
  }
  return [current, ...agents]
}

/** Picks one of the workspace's agents. */
export function AgentSelect({
  label,
  value,
  agents,
  disabled,
  onChange,
  className,
}: {
  label: string
  value: string
  agents: AgentOption[]
  disabled: boolean
  onChange: (agentPresetId: string) => void
  className?: string
}) {
  return (
    <Select value={value} onValueChange={onChange} disabled={disabled}>
      <SelectTrigger
        aria-label={label}
        className={cn("h-8 w-48 text-xs", className)}
      >
        <SelectValue placeholder="Choose an agent" />
      </SelectTrigger>
      <SelectContent>
        {agents.map((agent) => (
          <SelectItem key={agent.id} value={agent.id} className="text-xs">
            {agent.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

/** Marks a bot that runs on the customer's own app. */
export function CustomBadge() {
  return (
    <Badge variant="outline" className="px-1.5 py-0 text-[10px]">
      Custom
    </Badge>
  )
}
