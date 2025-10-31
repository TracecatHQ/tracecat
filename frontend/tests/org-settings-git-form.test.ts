import { gitFormSchema } from "@/components/organization/org-settings-git"

const baseFormData = {
  git_allowed_domains: [{ id: "0", text: "github.com" }],
  git_repo_url: null as string | null,
  git_repo_package_name: null as string | null,
}

describe("gitFormSchema git_repo_url superRefine", () => {
  it("accepts a valid git+ssh URL with nested groups", () => {
    const result = gitFormSchema.safeParse({
      ...baseFormData,
      git_repo_url: "git+ssh://git@gitlab.example.com/group/subgroup/repo.git",
    })

    expect(result.success).toBe(true)
    if (result.success) {
      expect(result.data.git_repo_url).toBe(
        "git+ssh://git@gitlab.example.com/group/subgroup/repo.git"
      )
    }
  })

  it.each([
    [
      "missing protocol",
      "ssh://git@github.com/org/repo.git",
      "URL must start with 'git+ssh://' protocol",
    ],
    [
      "missing git user",
      "git+ssh://github.com/org/repo.git",
      "URL must include 'git@' user specification",
    ],
    [
      "missing repository path",
      "git+ssh://git@github.com",
      "URL must include a repository path",
    ],
    [
      "missing port value",
      "git+ssh://git@github.com:/org/repo.git",
      "Missing port number after ':'",
    ],
    [
      "non numeric port",
      "git+ssh://git@github.com:notaport/org/repo.git",
      "Port must be numeric",
    ],
    [
      "insufficient path segments",
      "git+ssh://git@github.com/repo.git",
      "Repository path must have at least 2 segments (e.g., org/repo)",
    ],
  ])("returns specific error when %s", (_, url, message) => {
    const result = gitFormSchema.safeParse({
      ...baseFormData,
      git_repo_url: url,
    })

    expect(result.success).toBe(false)
    if (!result.success) {
      expect(result.error.issues.map((issue) => issue.message)).toContain(
        message
      )
    }
  })

  it("falls back to generic error when validation fails for other reasons", () => {
    const result = gitFormSchema.safeParse({
      ...baseFormData,
      git_repo_url: "git+ssh://git@github.com/org/repo.git@@",
    })

    expect(result.success).toBe(false)
    if (!result.success) {
      expect(result.error.issues.map((issue) => issue.message)).toContain(
        "Must be a valid Git SSH URL (e.g., git+ssh://git@github.com/org/repo.git)"
      )
    }
  })
})
