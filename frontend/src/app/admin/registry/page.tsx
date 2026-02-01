"use client"

import { PlatformRegistryReposTable } from "@/components/admin/platform-registry-repos-table"
import { PlatformRegistryStatus } from "@/components/admin/platform-registry-status"

export default function AdminRegistryPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Repositories
            </h2>
            <p className="text-md text-muted-foreground">
              Platform registry repositories and their sync status.
            </p>
          </div>
        </div>

        <PlatformRegistryStatus />
        <PlatformRegistryReposTable />
      </div>
    </div>
  )
}
