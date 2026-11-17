"use client"

import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { useMfaPolicy } from "@/hooks/use-mfa"

export function MfaPolicyCard({
  scope,
  description,
}: {
  scope: "organization" | "platform"
  description: string
}) {
  const { policy, isLoading, updatePolicy, isPending } = useMfaPolicy(scope)
  const id = `mfa-required-${scope}`
  const unavailable = policy ? !policy.webauthn_available : false
  return (
    <div className="flex items-start justify-between gap-6 rounded-md border p-4">
      <div className="space-y-1">
        <Label htmlFor={id} className="text-sm font-medium">
          Require security key
        </Label>
        <p className="text-sm text-muted-foreground">{description}</p>
        {unavailable && (
          <p className="text-sm text-destructive">
            {policy?.webauthn_unavailable_reason}
          </p>
        )}
      </div>
      <Switch
        id={id}
        checked={policy?.mfa_required ?? false}
        disabled={
          isLoading || isPending || (unavailable && !policy?.mfa_required)
        }
        onCheckedChange={(checked) => {
          void updatePolicy(checked).catch(() => undefined)
        }}
      />
    </div>
  )
}
