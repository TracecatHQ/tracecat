"use client"

import { RegistryActionsTable } from "@/components/registry/registry-actions-table"

export default function RegistryActionsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Actions</h2>
            <p className="text-md text-muted-foreground">
              View your organization&apos;s actions here.
            </p>
          </div>
        </div>
        <RegistryActionsTable />
      </div>
    </div>
  )
}
