"use client"

import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { useCallback } from "react"
import { serviceAccountsListWorkspaceServiceAccountApiKeys } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { AlertNotification } from "@/components/notifications"
import { ServiceAccountsManager } from "@/components/organization/service-accounts-manager"
import {
  useWorkspaceServiceAccountScopes,
  useWorkspaceServiceAccounts,
} from "@/hooks/use-service-accounts"
import { useWorkspaceId } from "@/providers/workspace-id"

const CREATE_SERVICE_ACCOUNT_PARAM = "createServiceAccount"

export function WorkspaceServiceAccounts() {
  const workspaceId = useWorkspaceId()
  const pathname = usePathname()
  const router = useRouter()
  const searchParams = useSearchParams()
  const canCreate = useScopeCheck("workspace:service_account:create")
  const canUpdate = useScopeCheck("workspace:service_account:update")
  const canDisable = useScopeCheck("workspace:service_account:disable")

  const {
    scopes,
    isLoading: scopesLoading,
    error: scopesError,
  } = useWorkspaceServiceAccountScopes(workspaceId)
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
    issueApiKey,
    issueApiKeyPending,
    revokeApiKey,
    revokeApiKeyPending,
  } = useWorkspaceServiceAccounts(workspaceId)

  const handleCreateSignalConsumed = useCallback(() => {
    if (!pathname || !searchParams?.get(CREATE_SERVICE_ACCOUNT_PARAM)) {
      return
    }
    const params = new URLSearchParams(searchParams.toString())
    params.delete(CREATE_SERVICE_ACCOUNT_PARAM)
    const next = params.toString()
    router.replace(next ? `${pathname}?${next}` : pathname, { scroll: false })
  }, [pathname, router, searchParams])

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
      kindLabel="Workspace"
      serviceAccounts={serviceAccounts}
      nextCursor={nextCursor}
      isLoading={isLoading || scopesLoading}
      error={error}
      availableScopes={scopes}
      createPending={createPending}
      updatePending={updatePending}
      disablePending={disablePending}
      enablePending={enablePending}
      issueApiKeyPending={issueApiKeyPending}
      revokeApiKeyPending={revokeApiKeyPending}
      apiKeysQueryKeyPrefix={["workspace-service-accounts", workspaceId]}
      canCreate={canCreate === true}
      canUpdate={canUpdate === true}
      canDisable={canDisable === true}
      openCreateSignal={searchParams?.get(CREATE_SERVICE_ACCOUNT_PARAM)}
      onCreateSignalConsumed={handleCreateSignalConsumed}
      onCreate={createServiceAccount}
      onUpdate={updateServiceAccount}
      onDisable={disableServiceAccount}
      onEnable={enableServiceAccount}
      onIssueApiKey={issueApiKey}
      onRevokeApiKey={revokeApiKey}
      listApiKeys={async (serviceAccountId) =>
        await serviceAccountsListWorkspaceServiceAccountApiKeys({
          workspaceId,
          serviceAccountId,
          limit: 100,
        })
      }
    />
  )
}
