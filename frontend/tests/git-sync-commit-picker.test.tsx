/**
 * @jest-environment jsdom
 */

import { render, screen } from "@testing-library/react"
import type { GitCommitInfo } from "@/client"
import { GitSyncCommitPicker } from "@/components/workspace-sync/git-sync-commit-picker"

const COMMITS: GitCommitInfo[] = [
  {
    sha: "a".repeat(40),
    message: "Latest change",
    author: "Example author",
    author_email: "author@example.com",
    date: "2026-01-01T00:00:00Z",
  },
]

describe("GitSyncCommitPicker", () => {
  it("keeps a selected commit visible after it leaves the list", () => {
    render(
      <GitSyncCommitPicker
        commits={COMMITS}
        commitSha={"b".repeat(40)}
        isLoading={false}
        hasError={false}
        onSelectCommit={jest.fn()}
      />
    )

    expect(
      screen.getByRole("combobox", { name: "Commit bbbbbbb" })
    ).toHaveTextContent("bbbbbbbNot in recent commits")
    expect(screen.queryByText("Pick a commit")).not.toBeInTheDocument()
  })
})
