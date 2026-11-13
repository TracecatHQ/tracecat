"use client"

import { useId } from "react"
import type { ChannelBotProvider } from "@/client"
import {
  CHANNEL_BOT_PROVIDER_NAMES,
  isUnreachableEndpoint,
} from "@/components/agents/external-channels/channel-bot-utils"
import { CopyButton } from "@/components/copy-button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

/** A read-only value to paste into the provider, with a copy control. */
export function CopyField({
  label,
  value,
  help,
  warning,
}: {
  label: string
  value: string
  help?: string
  /** Shown under the input and read as its description. */
  warning?: string | null
}) {
  const id = useId()
  const warningId = `${id}-warning`
  return (
    <div className="space-y-2">
      <Label htmlFor={id} className="text-xs">
        {label}
      </Label>
      <div className="flex items-center gap-2">
        <Input
          id={id}
          readOnly
          value={value}
          aria-describedby={warning ? warningId : undefined}
        />
        <CopyButton
          value={value}
          toastMessage={`${label} copied`}
          tooltipMessage={`Copy ${label.toLowerCase()}`}
        />
      </div>
      {warning ? (
        <p id={warningId} className="text-xs text-destructive">
          {warning}
        </p>
      ) : null}
      {help ? <p className="text-xs text-muted-foreground">{help}</p> : null}
    </div>
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
      />
      {help ? <p className="text-xs text-muted-foreground">{help}</p> : null}
    </div>
  )
}
