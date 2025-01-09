"use client"

import { Separator } from "@/components/ui/separator"
import { OrgSettingsSsoForm } from "@/components/organization/org-settings-sso"

export default function SsoSettingsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="space-y-8">
          <div className="flex w-full">
            <div className="items-start space-y-3 text-left">
              <h2 className="text-2xl font-semibold tracking-tight">
                SAML Single Sign-On
              </h2>
              <p className="text-md text-muted-foreground">
                View and manage your organization SAML SSO settings here.
              </p>
            </div>
          </div>
          <Separator />
        </div>
        <OrgSettingsSsoForm />
      </div>
    </div>
  )
}
