"use client"

import { PlatformRegistryReposTable } from "@/components/admin/platform-registry-repos-table"
import { PlatformRegistrySettings } from "@/components/admin/platform-registry-settings"
import { PlatformRegistryStatus } from "@/components/admin/platform-registry-status"
import { PlatformRegistryVersionsTable } from "@/components/admin/platform-registry-versions-table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

export default function AdminRegistryPage() {
  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">
          Platform registry
        </h1>
        <p className="text-muted-foreground">
          Manage the platform-wide action registry, sync repositories, and
          promote versions.
        </p>
      </div>

      <PlatformRegistryStatus />

      <Tabs defaultValue="repositories" className="space-y-4">
        <TabsList>
          <TabsTrigger value="repositories">Repositories</TabsTrigger>
          <TabsTrigger value="versions">Versions</TabsTrigger>
          <TabsTrigger value="settings">Settings</TabsTrigger>
        </TabsList>
        <TabsContent value="repositories" className="space-y-4">
          <div>
            <h2 className="text-lg font-medium mb-2">Repositories</h2>
            <p className="text-sm text-muted-foreground mb-4">
              Platform registry repositories and their sync status.
            </p>
          </div>
          <PlatformRegistryReposTable />
        </TabsContent>
        <TabsContent value="versions" className="space-y-4">
          <div>
            <h2 className="text-lg font-medium mb-2">Versions</h2>
            <p className="text-sm text-muted-foreground mb-4">
              Registry versions across all repositories. Promote a version to
              make it the current active version.
            </p>
          </div>
          <PlatformRegistryVersionsTable />
        </TabsContent>
        <TabsContent value="settings" className="space-y-4">
          <PlatformRegistrySettings />
        </TabsContent>
      </Tabs>
    </div>
  )
}
