"use client"

import { GitHubAppSetup } from "@/components/organization/org-vcs-github"

export function OrgVCSSettings() {
  return (
    <div className="space-y-8">
      <GitHubAppSetup />
    </div>
  )
}
