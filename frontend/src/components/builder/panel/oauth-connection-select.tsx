"use client"

import { useFormContext, useWatch } from "react-hook-form"
import type {
  OAuthConnectionRef,
  RegistryOAuthSecret,
  RegistrySecretType,
} from "@/client"
import { ControlFlowField } from "@/components/builder/panel/control-flow-fields"
import {
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from "@/components/ui/form"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useIntegrations } from "@/lib/hooks"
import { findProviderForIntegration } from "@/lib/integrations"
import { useWorkspaceId } from "@/providers/workspace-id"

const AUTO_VALUE = "__auto__"

type OAuthConnectionForm = { oauth_connection?: OAuthConnectionRef }

function connectionKey(connection: OAuthConnectionRef): string {
  return `${connection.provider_id}:${connection.grant_type}`
}

function sameConnection(a: OAuthConnectionRef, b: OAuthConnectionRef) {
  return a.provider_id === b.provider_id && a.grant_type === b.grant_type
}

/**
 * Action panel field for the preferred OAuth connection. Shown when the action
 * declares more than one OAuth connection, or a preference is already saved.
 */
export function OAuthConnectionField({
  secrets,
}: {
  secrets: RegistrySecretType[]
}) {
  const { control } = useFormContext<OAuthConnectionForm>()
  const value = useWatch({ control, name: "oauth_connection" })
  const oauthSecrets = secrets.filter(
    (secret): secret is RegistryOAuthSecret => secret.type === "oauth"
  )
  if (oauthSecrets.length < 2 && !value) {
    return null
  }
  return (
    <ControlFlowField
      label="OAuth connection"
      description="Prefer this connection. Falls back to the default order if it isn't connected."
    >
      <FormField
        name="oauth_connection"
        control={control}
        render={({ field }) => (
          <FormItem>
            <FormMessage className="whitespace-pre-line" />
            <FormControl>
              <OAuthConnectionSelect
                oauthSecrets={oauthSecrets}
                value={field.value}
                onChange={field.onChange}
              />
            </FormControl>
          </FormItem>
        )}
      />
    </ControlFlowField>
  )
}

/**
 * Picks which of an action's declared OAuth connections to prefer. A saved
 * pick stays selectable even when it is not connected or not declared.
 */
function OAuthConnectionSelect({
  oauthSecrets,
  value,
  onChange,
}: {
  oauthSecrets: RegistryOAuthSecret[]
  value: OAuthConnectionRef | undefined
  onChange: (value: OAuthConnectionRef | undefined) => void
}) {
  const workspaceId = useWorkspaceId()
  const { integrations, providers } = useIntegrations(workspaceId)

  const declared: OAuthConnectionRef[] = oauthSecrets.map((secret) => ({
    provider_id: secret.provider_id,
    grant_type: secret.grant_type,
  }))
  const options = [...declared]
  if (value && !declared.some((option) => sameConnection(option, value))) {
    options.push(value)
  }

  function label(connection: OAuthConnectionRef): string {
    return (
      findProviderForIntegration(providers, connection)?.name ??
      `${connection.provider_id} (${connection.grant_type})`
    )
  }

  function status(connection: OAuthConnectionRef): string | null {
    if (!declared.some((option) => sameConnection(option, connection))) {
      return "Not used by this action"
    }
    if (integrations === undefined) {
      return null
    }
    const connected = integrations.some(
      (integration) =>
        sameConnection(integration, connection) &&
        integration.status === "connected"
    )
    return connected ? null : "Not connected"
  }

  return (
    <Select
      value={value ? connectionKey(value) : AUTO_VALUE}
      onValueChange={(next) =>
        onChange(options.find((option) => connectionKey(option) === next))
      }
    >
      <SelectTrigger className="h-8 text-xs">
        <SelectValue placeholder="Auto (default order)" />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={AUTO_VALUE} className="text-xs">
          Auto (default order)
        </SelectItem>
        {options.map((option) => {
          const optionStatus = status(option)
          return (
            <SelectItem
              key={connectionKey(option)}
              value={connectionKey(option)}
              className="text-xs"
            >
              {label(option)}
              {optionStatus && (
                <span className="ml-2 text-muted-foreground">
                  {optionStatus}
                </span>
              )}
            </SelectItem>
          )
        })}
      </SelectContent>
    </Select>
  )
}
