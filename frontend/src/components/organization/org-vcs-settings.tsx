"use client"

import { BitbucketTokenSetup } from "@/components/organization/org-vcs-bitbucket"
import { BitbucketDataCenterTokenSetup } from "@/components/organization/org-vcs-bitbucket-data-center"
import { GitHubAppSetup } from "@/components/organization/org-vcs-github"
import { GitLabTokenSetup } from "@/components/organization/org-vcs-gitlab"

export function OrgVCSSettings() {
  return (
    <div className="space-y-4">
      <GitHubAppSetup />
      <GitLabTokenSetup />
      <BitbucketTokenSetup />
      <BitbucketDataCenterTokenSetup />
    </div>
  )
}
