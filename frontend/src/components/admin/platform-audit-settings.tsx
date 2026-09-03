"use client"

import { AuditSettingsForm } from "@/components/organization/org-settings-audit"
import { useAdminAuditSettings } from "@/hooks/use-admin"

export function PlatformAuditSettings() {
  const {
    auditSettings,
    auditSettingsIsLoading,
    auditSettingsError,
    updateAuditSettings,
    updateAuditSettingsIsPending,
    testAuditWebhook,
    testAuditWebhookIsPending,
  } = useAdminAuditSettings()

  return (
    <AuditSettingsForm
      auditSettings={auditSettings}
      auditSettingsIsLoading={auditSettingsIsLoading}
      auditSettingsError={auditSettingsError}
      updateAuditSettings={updateAuditSettings}
      updateAuditSettingsIsPending={updateAuditSettingsIsPending}
      testAuditWebhook={testAuditWebhook}
      testAuditWebhookIsPending={testAuditWebhookIsPending}
      decryptFailureTitle="Unable to decrypt platform settings"
    />
  )
}
