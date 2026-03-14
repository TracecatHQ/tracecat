"use client"

import { notFound } from "next/navigation"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { WorkspaceServiceAccounts } from "@/components/organization/workspace-service-accounts"

export default function WorkspaceServiceAccountsPage() {
  const canReadServiceAccounts = useScopeCheck("workspace:service_account:read")

  if (canReadServiceAccounts === false) {
    notFound()
  }

  return <WorkspaceServiceAccounts />
}
