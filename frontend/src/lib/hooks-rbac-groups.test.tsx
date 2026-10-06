import { act, renderHook } from "@testing-library/react"
import type { ReactNode } from "react"
import { rbacAddGroupMember } from "@/client"
import { toast } from "@/components/ui/use-toast"
import { useRbacGroups } from "@/lib/hooks"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  ...jest.requireActual("@/client"),
  rbacAddGroupMember: jest.fn(),
  rbacListGroups: jest.fn(async () => ({ items: [] })),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

function apiError(status: number, detail?: unknown): Error {
  return Object.assign(new Error("Request failed"), {
    status,
    body: { detail },
  })
}

function renderRbacGroups() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  const invalidateQueries = jest.spyOn(queryClient, "invalidateQueries")
  const { result } = renderHook(() => useRbacGroups(), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  })
  return { result, invalidateQueries }
}

describe("useRbacGroups addGroupMembers", () => {
  beforeEach(() => {
    jest.mocked(rbacAddGroupMember).mockReset()
    jest.mocked(toast).mockReset()
  })

  it("adds users one at a time in order", async () => {
    const events: string[] = []
    let inFlight = 0
    let maxInFlight = 0
    jest.mocked(rbacAddGroupMember).mockImplementation((async ({
      requestBody,
    }: {
      requestBody: { user_id: string }
    }) => {
      inFlight += 1
      maxInFlight = Math.max(maxInFlight, inFlight)
      events.push(`start ${requestBody.user_id}`)
      await new Promise((resolve) => setTimeout(resolve, 0))
      events.push(`end ${requestBody.user_id}`)
      inFlight -= 1
    }) as unknown as typeof rbacAddGroupMember)
    const { result, invalidateQueries } = renderRbacGroups()

    let outcome: unknown
    await act(async () => {
      outcome = await result.current.addGroupMembers({
        groupId: "group-1",
        userIds: ["user-1", "user-2", "user-3"],
      })
    })

    expect(maxInFlight).toBe(1)
    expect(events).toEqual([
      "start user-1",
      "end user-1",
      "start user-2",
      "end user-2",
      "start user-3",
      "end user-3",
    ])
    expect(rbacAddGroupMember).toHaveBeenNthCalledWith(1, {
      groupId: "group-1",
      requestBody: { user_id: "user-1" },
    })
    expect(outcome).toEqual({ failedUserIds: [], failures: [] })
    expect(invalidateQueries).toHaveBeenCalledTimes(2)
    expect(toast).toHaveBeenCalledTimes(1)
    expect(toast).toHaveBeenCalledWith({ title: "3 members added" })
  })

  it("keeps going after a failure and reports the distinct reasons", async () => {
    jest
      .mocked(rbacAddGroupMember)
      .mockRejectedValueOnce(apiError(409))
      .mockResolvedValueOnce(undefined as never)
      .mockRejectedValueOnce(apiError(409))
      .mockRejectedValueOnce(apiError(400, "User is not an org member"))
      .mockRejectedValueOnce(apiError(403))
      .mockRejectedValueOnce(apiError(404))
      .mockRejectedValueOnce(new Error("Network down"))
    const { result, invalidateQueries } = renderRbacGroups()

    let outcome: unknown
    await act(async () => {
      outcome = await result.current.addGroupMembers({
        groupId: "group-1",
        userIds: ["u1", "u2", "u3", "u4", "u5", "u6", "u7"],
      })
    })

    expect(rbacAddGroupMember).toHaveBeenCalledTimes(7)
    expect(outcome).toEqual({
      failedUserIds: ["u1", "u3", "u4", "u5", "u6", "u7"],
      failures: [
        {
          userId: "u1",
          reason: "This user is already a member of the group.",
        },
        {
          userId: "u3",
          reason: "This user is already a member of the group.",
        },
        { userId: "u4", reason: "User is not an org member" },
        {
          userId: "u5",
          reason: "You don't have permission to add group members.",
        },
        { userId: "u6", reason: "The group or user does not exist." },
        { userId: "u7", reason: "Failed to add member" },
      ],
    })
    expect(invalidateQueries).toHaveBeenCalledTimes(2)
    expect(toast).toHaveBeenCalledTimes(1)
    expect(toast).toHaveBeenCalledWith({
      title: "Added 1 of 7 members",
      description: [
        "This user is already a member of the group.",
        "User is not an org member",
        "You don't have permission to add group members.",
        "The group or user does not exist.",
        "Failed to add member",
      ].join("\n"),
      variant: "destructive",
    })
  })
})
