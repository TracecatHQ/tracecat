"use client"

import { ReleaseSteps } from "./components/release-steps"
import { SourceSection } from "./components/source-section"
import { TargetsSection } from "./components/targets-section"
import { VersionsSection } from "./components/versions-section"
import {
  useManagedSource,
  useManagedTags,
  useManagedTargets,
  useReleaseCandidate,
} from "./use-managed-package"

/** Platform admin page for publishing and rolling out the managed package. */
export default function AdminManagedPackagePage() {
  const { source } = useManagedSource()
  const hasSource = source !== null
  const tagsQuery = useManagedTags({ enabled: hasSource })
  const targetsQuery = useManagedTargets()
  const { candidate } = useReleaseCandidate({ enabled: hasSource })

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Managed package
            </h2>
            <p className="text-base text-muted-foreground">
              Three separate steps: publish the package from the source
              workspace, cut a release tag, then deploy that tag to customer
              workspaces. Customers change only at the last step.
            </p>
          </div>
        </div>

        {hasSource && (
          <ReleaseSteps
            candidate={candidate}
            tags={tagsQuery.tags}
            targets={targetsQuery.targets}
          />
        )}

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
