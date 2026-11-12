"use client"

import { OrgSettingsPythonPackagesForm } from "@/components/organization/org-settings-python-packages"

export default function PythonPackagesSettingsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Python packages
            </h2>
            <p className="text-base text-muted-foreground">
              Install Python script dependencies from your own package index,
              such as a JFrog Artifactory PyPI repository, instead of PyPI.
            </p>
          </div>
        </div>

        <OrgSettingsPythonPackagesForm />
      </div>
    </div>
  )
}
