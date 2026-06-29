/**
 * @jest-environment jsdom
 */

import { render } from "@testing-library/react"
import { WorkflowPullDialog } from "@/components/organization/workflow-pull-dialog"
import {
  useRepositoryCommits,
  useWorkflowSync,
} from "@/hooks/use-workspace-sync"

const mockPullWorkflows = jest.fn()

jest.mock("@/hooks/use-workspace-sync", () => ({
  useRepositoryCommits: jest.fn(),
  useWorkflowSync: jest.fn(),
}))

jest.mock("@/components/registry/commit-selector", () => ({
  CommitSelector: () => null,
}))

describe("WorkflowPullDialog", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.mocked(useWorkflowSync).mockReturnValue({
      pullWorkflows: mockPullWorkflows,
      pullWorkflowsIsPending: false,
      pullWorkflowsError: null,
    } as ReturnType<typeof useWorkflowSync>)
    jest.mocked(useRepositoryCommits).mockReturnValue({
      commits: [],
      commitsIsLoading: false,
      commitsError: null,
    } as ReturnType<typeof useRepositoryCommits>)
  })

  it("loads commits from the branch encoded in the workspace git URL", () => {
    render(
      <WorkflowPullDialog
        open={true}
        onOpenChange={jest.fn()}
        workspaceId="workspace-1"
        gitRepoUrl="git+ssh://git@github.com/test-org/repo-b.git@trunk"
      />
    )

    expect(useRepositoryCommits).toHaveBeenCalledWith("workspace-1", {
      branch: "trunk",
      enabled: true,
    })
  })
})
