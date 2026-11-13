import { getGitHubAppManifestUrl } from "@/components/organization/org-vcs-github"

describe("getGitHubAppManifestUrl", () => {
  it("creates an organization-owned app when an organization is named", () => {
    expect(getGitHubAppManifestUrl("https://github.com", "acme-sec")).toBe(
      "https://github.com/organizations/acme-sec/settings/apps/new"
    )
  })

  it("creates a personal app when the organization is blank", () => {
    expect(getGitHubAppManifestUrl("https://github.com", "")).toBe(
      "https://github.com/settings/apps/new"
    )
    expect(getGitHubAppManifestUrl("https://github.com", "   ")).toBe(
      "https://github.com/settings/apps/new"
    )
  })

  it("keeps the GitHub Enterprise host", () => {
    expect(getGitHubAppManifestUrl("https://github.example.com", "")).toBe(
      "https://github.example.com/settings/apps/new"
    )
    expect(
      getGitHubAppManifestUrl("https://github.example.com", "sec team")
    ).toBe(
      "https://github.example.com/organizations/sec%20team/settings/apps/new"
    )
  })
})
