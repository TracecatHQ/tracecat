"use client"

import { GitHubAppSetup } from "./org-vcs-github"

export function OrgVCSSettings() {
  return (
    <div className="space-y-8">
      <GitHubAppSetup />
    </div>
  )
}
