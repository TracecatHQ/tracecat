"use client"

import { BitbucketSettings } from "@/components/organization/org-vcs-bitbucket-settings"
import { GitHubAppSetup } from "@/components/organization/org-vcs-github"
import { GitLabTokenSetup } from "@/components/organization/org-vcs-gitlab"

export function OrgVCSSettings() {
  return (
    <div className="space-y-4">
      <GitHubAppSetup />
      <GitLabTokenSetup />
      <BitbucketSettings />
    </div>
  )
}
