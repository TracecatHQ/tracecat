"use client"

import { OrgSettingsSecretStores } from "@/components/organization/org-settings-secret-stores"

export default function SecretStoresSettingsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Secret stores
            </h2>
            <p className="text-base text-muted-foreground">
              Let workspaces reference secrets that stay in AWS Secrets Manager.
            </p>
          </div>
        </div>
        <OrgSettingsSecretStores />
      </div>
    </div>
  )
}
