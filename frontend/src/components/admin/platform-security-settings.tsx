"use client"

import { IPAllowlistSettingsForm } from "@/components/organization/org-settings-security"
import { useAdminSecuritySettings } from "@/hooks/use-admin"

export function PlatformSecuritySettings() {
  return (
    <IPAllowlistSettingsForm
      {...useAdminSecuritySettings()}
      enforcementDescription="Only allow platform admin console and API access from the IP ranges in the allowlists below. Organization access is unaffected. Your current IP must be covered before enforcement can be enabled."
    />
  )
}
