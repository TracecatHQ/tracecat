"use client"

import { PlatformBuildInfo } from "@/components/admin/platform-build-info"

export default function AdminBuildPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Build</h2>
            <p className="text-base text-muted-foreground">
              Version and build details of this deployment.
            </p>
          </div>
        </div>

        <PlatformBuildInfo />
      </div>
    </div>
  )
}
