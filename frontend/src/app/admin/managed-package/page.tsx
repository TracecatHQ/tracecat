"use client"

import {
  type ReleaseStepDataState,
  ReleaseSteps,
} from "./components/release-steps"
import { SourceSection } from "./components/source-section"
import { TargetsSection } from "./components/targets-section"
import { VersionsSection } from "./components/versions-section"
import {
  useManagedSource,
  useManagedTags,
  useManagedTargets,
  useReleaseCandidate,
} from "./use-managed-package"

function dataState(query: {
  isLoading: boolean
  error: unknown
}): ReleaseStepDataState {
  if (query.isLoading) {
    return "loading"
  }
  if (query.error) {
    return "error"
  }
  return "ready"
}

/** Platform admin page for publishing and rolling out the managed package. */
export default function AdminManagedPackagePage() {
  const { source } = useManagedSource()
  const hasSource = source !== null
  const tagsQuery = useManagedTags({ enabled: hasSource })
  const targetsQuery = useManagedTargets()
  const candidateQuery = useReleaseCandidate({ enabled: hasSource })

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
            candidate={candidateQuery.candidate}
            candidateState={dataState(candidateQuery)}
            tags={tagsQuery.tags}
            tagsState={dataState(tagsQuery)}
            targets={targetsQuery.targets}
            targetsState={dataState(targetsQuery)}
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
