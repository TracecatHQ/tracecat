import {
  getWorkspaceSyncPushButtonLabel,
  getWorkspaceSyncPushOutcome,
  getWorkspaceSyncPushResultLabel,
  getWorkspaceSyncPushWarning,
} from "@/components/workspace-sync/push-target-policy"

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

  it("uses GitLab merge request wording when provider is GitLab", () => {
    const outcome = getWorkspaceSyncPushOutcome({
      mode: "pull-request",
      targetBranch: "sync/workspace",
      defaultBranch: "main",
      isCreatingBranch: true,
    })

    expect(
      getWorkspaceSyncPushButtonLabel({
        outcome,
        isCreatingBranch: true,
        isPending: false,
        provider: "gitlab",
      })
    ).toBe("Push & open MR")
    expect(
      getWorkspaceSyncPushResultLabel({
        outcome,
        defaultBranch: "main",
        provider: "gitlab",
      })
    ).toBe("MR into main")
    expect(
      getWorkspaceSyncPushWarning({
        outcome: {
          ...outcome,
          createPr: false,
          willCreatePr: false,
          targetIsDefault: true,
        },
        defaultBranch: "main",
        provider: "gitlab",
      })
    ).toBe("This commits directly to main. No merge request will be created.")
  })
})
