import { syncSettingsSchema } from "@/components/settings/workspace-sync-settings"

describe("syncSettingsSchema git_repo_url validation", () => {
  it.each([
    "git+ssh://git@github.com/org/repo.git",
    "git+ssh://git@gitlab.company.com:2222/team/project.git",
    "git+ssh://git@gitlab.com/group/subgroup/repo.git",
    "git+ssh://git@example.com/org/repo",
  ])("accepts valid git SSH URL %s", (url) => {
    const result = syncSettingsSchema.safeParse({
      git_repo_url: url,
    })

    expect(result.success).toBe(true)
    if (result.success) {
      expect(result.data.git_repo_url).toBe(url)
    }
  })

  it.each([
    [
      "protocol",
      "https://github.com/org/repo.git",
      "URL must start with 'git+ssh://' protocol",
    ],
    [
      "user",
      "git+ssh://user@github.com/org/repo.git",
      "URL must include 'git@' user specification",
    ],
    ["path", "git+ssh://git@github.com", "URL must include a repository path"],
    [
      "port text",
      "git+ssh://git@github.com:not_a_port/org/repo.git",
      "Port must be numeric",
    ],
    [
      "port missing",
      "git+ssh://git@github.com:/org/repo.git",
      "Missing port number after ':'",
    ],
    [
      "segments",
      "git+ssh://git@github.com/repo.git",
      "Repository path must have at least 2 segments (e.g., org/repo)",
    ],
    [
      "trailing ref",
      "git+ssh://git@github.com/org/repo.git@@",
      "Must be a valid Git SSH URL (e.g., git+ssh://git@github.com/org/repo.git)",
    ],
  ])("rejects invalid git SSH URL with bad %s", (_, url, message) => {
    const result = syncSettingsSchema.safeParse({
      git_repo_url: url,
    })

    expect(result.success).toBe(false)
    if (!result.success) {
      expect(
        result.error.issues.map((issue: { message: string }) => issue.message)
      ).toContain(message)
    }
  })
})
