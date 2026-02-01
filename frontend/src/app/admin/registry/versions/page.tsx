"use client"

import { PlatformRegistryVersionsTable } from "@/components/admin/platform-registry-versions-table"

export default function AdminRegistryVersionsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Versions</h2>
            <p className="text-md text-muted-foreground">
              Registry versions across all repositories. Promote a version to
              make it the current active version.
            </p>
          </div>
        </div>

        <PlatformRegistryVersionsTable />
      </div>
    </div>
  )
}
