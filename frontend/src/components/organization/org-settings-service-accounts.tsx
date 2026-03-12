"use client"

import { AlertNotification } from "@/components/notifications"
import { ServiceAccountsManager } from "@/components/organization/service-accounts-manager"
import {
  useOrganizationServiceAccountScopes,
  useOrganizationServiceAccounts,
} from "@/hooks/use-service-accounts"

export function OrgSettingsServiceAccounts() {
  const {
    scopes,
    isLoading: scopesLoading,
    error: scopesError,
  } = useOrganizationServiceAccountScopes()
  const {
    serviceAccounts,
    nextCursor,
    isLoading,
    error,
    createServiceAccount,
    createPending,
    updateServiceAccount,
    updatePending,
    disableServiceAccount,
    disablePending,
    enableServiceAccount,
    enablePending,
    regenerateKey,
    regeneratePending,
    revokeKey,
    revokePending,
  } = useOrganizationServiceAccounts()

  if (scopesError) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading scopes: ${scopesError.message}`}
      />
    )
  }

  return (
    <ServiceAccountsManager
      kindLabel="Organization"
      serviceAccounts={serviceAccounts}
      nextCursor={nextCursor}
      isLoading={isLoading || scopesLoading}
      error={error}
      availableScopes={scopes}
      createPending={createPending}
      updatePending={updatePending}
      disablePending={disablePending}
      enablePending={enablePending}
      regeneratePending={regeneratePending}
      revokePending={revokePending}
      onCreate={createServiceAccount}
      onUpdate={updateServiceAccount}
      onDisable={disableServiceAccount}
      onEnable={enableServiceAccount}
      onRegenerate={regenerateKey}
      onRevoke={revokeKey}
    />
  )
}
