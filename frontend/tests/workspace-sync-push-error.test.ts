import { getWorkspaceSyncPushErrorNotice } from "@/components/workspace-sync/push-error"

describe("getWorkspaceSyncPushErrorNotice", () => {
  it("warns that a gateway timeout may still complete", () => {
    const notice = getWorkspaceSyncPushErrorNotice(
      Object.assign(new Error("Gateway Timeout"), { status: 504 })
    )

    expect(notice).toEqual({
      title: "Push is taking longer than expected",
      description:
        "The request timed out, but the push may still complete. Check the target branch before trying again.",
      isDestructive: false,
    })
  })

  it("preserves the API detail for genuine failures", () => {
    const notice = getWorkspaceSyncPushErrorNotice(
      Object.assign(new Error("Bad Request"), {
        status: 400,
        body: { detail: "The branch name is invalid" },
      })
    )

    expect(notice).toEqual({
      title: "Push failed",
      description: "The branch name is invalid",
      isDestructive: true,
    })
  })

  it("falls back to the error message when no API detail is available", () => {
    expect(
      getWorkspaceSyncPushErrorNotice(new Error("Connection closed"))
    ).toEqual({
      title: "Push failed",
      description: "Connection closed",
      isDestructive: true,
    })
  })
})
