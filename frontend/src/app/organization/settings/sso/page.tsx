"use client"

import { OrgSettingsSsoForm } from "@/components/organization/org-settings-sso"

export default function SsoSettingsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              SAML SSO
            </h2>
            <p className="text-md text-muted-foreground">
              View and manage your organization SAML SSO settings here.
            </p>
          </div>
        </div>

        <OrgSettingsSsoForm />
      </div>
    </div>
  )
}
