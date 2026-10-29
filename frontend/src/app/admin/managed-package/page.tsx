"use client"

import { SourceSection } from "./components/source-section"
import { TargetsSection } from "./components/targets-section"
import { VersionsSection } from "./components/versions-section"
import {
  useManagedSource,
  useManagedTags,
  useManagedTargets,
} from "./use-managed-package"

/** Platform admin page for publishing and rolling out the managed package. */
export default function AdminManagedPackagePage() {
  const { source } = useManagedSource()
  const hasSource = source !== null
  const tagsQuery = useManagedTags({ enabled: hasSource })
  const targetsQuery = useManagedTargets()

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Managed package
            </h2>
            <p className="text-base text-muted-foreground">
              Publish a curated package from a source workspace, cut releases,
              and roll them out to customer workspaces.
            </p>
          </div>
        </div>

        <SourceSection />

        <VersionsSection
          tags={tagsQuery.tags}
          targets={targetsQuery.targets}
          isLoading={tagsQuery.isLoading}
          error={tagsQuery.error}
          hasSource={hasSource}
        />

        <TargetsSection
          targets={targetsQuery.targets}
          tags={tagsQuery.tags}
          isLoading={targetsQuery.isLoading}
          error={targetsQuery.error}
          hasSource={hasSource}
        />
      </div>
    </div>
  )
}
