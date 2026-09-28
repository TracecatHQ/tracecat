import type { GitBranchInfo } from "@/client"
import {
  getWorkspaceSyncBaseBranch,
  getWorkspaceSyncConfiguredRef,
} from "@/components/workspace-sync/branch-target-selector"

const branches: GitBranchInfo[] = [
  { name: "main", is_default: true },
  { name: "develop", is_default: false },
]

describe("getWorkspaceSyncConfiguredRef", () => {
  it("extracts branch refs from workspace sync Git URLs", () => {
    expect(
      getWorkspaceSyncConfiguredRef(
        "git+ssh://git@github.com/test-org/repo.git@develop"
      )
    ).toBe("develop")
  })

  it("extracts branch refs from Git URLs without a .git suffix", () => {
    expect(
      getWorkspaceSyncConfiguredRef(
        "git+ssh://git@github.com/test-org/repo@develop"
      )
    ).toBe("develop")
  })

  it("returns undefined when the Git URL has no branch ref", () => {
    expect(
      getWorkspaceSyncConfiguredRef(
        "git+ssh://git@github.com/test-org/repo.git"
      )
    ).toBeUndefined()
  })

  it("returns undefined for suffixless Git URLs without a branch ref", () => {
    expect(
      getWorkspaceSyncConfiguredRef("git+ssh://git@github.com/test-org/repo")
    ).toBeUndefined()
  })
})

describe("getWorkspaceSyncBaseBranch", () => {
  it("prefers the configured Git URL ref over the repository default branch", () => {
    expect(
      getWorkspaceSyncBaseBranch(
        "git+ssh://git@github.com/test-org/repo.git@develop",
        branches
      )
    ).toBe("develop")
  })

  it("prefers configured refs from Git URLs without a .git suffix", () => {
    expect(
      getWorkspaceSyncBaseBranch(
        "git+ssh://git@github.com/test-org/repo@develop",
        branches
      )
    ).toBe("develop")
  })

  it("falls back to the repository default branch", () => {
    expect(
      getWorkspaceSyncBaseBranch(
        "git+ssh://git@github.com/test-org/repo.git",
        branches
      )
    ).toBe("main")
  })
})
