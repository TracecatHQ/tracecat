import { GIT_SSH_URL_REGEX, getRepoRef } from "@/lib/git"

describe("GIT_SSH_URL_REGEX", () => {
  const validUrls = [
    "git+ssh://git@github.com/user/repo.git",
    "git+ssh://git@gitlab.company.com/team/project.git",
    "git+ssh://git@example.com/org/repo.git",
    "git+ssh://someuser@git.example.com/org/repo.git",
    "git+ssh://git@github.com:2222/user/repo.git",
    "git+ssh://git@gitlab.com/org/team/subteam/repo.git",
    "git+ssh://git@github.com/user/repo",
    "git+ssh://git@github.com/user/repo.git@main",
    "git+ssh://git@github.com/user/repo.git@feature/custom-branch",
  ]

  const invalidUrls = [
    "git+ssh://git@/user/repo.git",
    "https://github.com/user/repo.git",
    "ssh://git@github.com/user/repo.git",
    "git+ssh://github.com/user/repo.git", // Missing SSH user
    "git+ssh://git@github.com:not_a_port/user/repo.git", // Invalid port
    "git+ssh://git@github.com/repo.git", // Missing org segment
    "git+ssh://git@github.com:/org/repo/subdir.git", // Missing port
  ]

  it.each(validUrls)("accepts valid git SSH URL %s", (url) => {
    expect(GIT_SSH_URL_REGEX.test(url)).toBe(true)
  })

  it.each(invalidUrls)("rejects invalid git SSH URL %s", (url) => {
    expect(GIT_SSH_URL_REGEX.test(url)).toBe(false)
  })
})

describe("getRepoRef", () => {
  it("returns a ref that contains a slash", () => {
    expect(
      getRepoRef("git+ssh://git@github.com/org/repo.git@feature/foo")
    ).toBe("feature/foo")
  })

  it("returns a plain branch ref", () => {
    expect(getRepoRef("git+ssh://git@github.com/org/repo.git@main")).toBe(
      "main"
    )
  })

  it("returns null when the URL has no ref", () => {
    expect(getRepoRef("git+ssh://git@github.com/org/repo.git")).toBeNull()
  })

  it("returns null for an unparseable URL", () => {
    expect(getRepoRef("https://github.com/org/repo.git@main")).toBeNull()
  })
})
