import { GIT_SSH_URL_REGEX } from "@/lib/git"

describe("GIT_SSH_URL_REGEX", () => {
  const validUrls = [
    "git+ssh://git@github.com/user/repo.git",
    "git+ssh://git@gitlab.company.com/team/project.git",
    "git+ssh://git@example.com/org/repo.git",
    "git+ssh://git@github.com:2222/user/repo.git",
    "git+ssh://git@gitlab.com/org/team/subteam/repo.git",
    "git+ssh://git@github.com/user/repo",
    "git+ssh://git@github.com/user/repo.git@main",
  ]

  const invalidUrls = [
    "git+ssh://git@/user/repo.git",
    "https://github.com/user/repo.git",
    "ssh://git@github.com/user/repo.git",
    "git+ssh://github.com/user/repo.git", // Missing git@ user
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
