import { getWorkspaceSyncPushOutcome } from "@/components/workspace-sync/push-target-policy"

describe("getWorkspaceSyncPushOutcome", () => {
  it("blocks pull request mode when a new branch name matches the default branch", () => {
    const outcome = getWorkspaceSyncPushOutcome({
      mode: "pull-request",
      targetBranch: "main",
      defaultBranch: "main",
      isCreatingBranch: true,
    })

    expect(outcome.targetIsDefault).toBe(true)
    expect(outcome.isPullRequestBlocked).toBe(true)
    expect(outcome.willCreatePr).toBe(false)
  })
})
