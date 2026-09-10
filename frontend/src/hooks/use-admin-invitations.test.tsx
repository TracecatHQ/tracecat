import { act, renderHook } from "@testing-library/react"
import type { ReactNode } from "react"
import {
  adminListOrganizationInvitations,
  adminResendOrganizationInvitation,
} from "@/client"
import { useAdminOrgInvitations } from "@/hooks/use-admin"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  adminListOrganizationInvitations: jest.fn(),
  adminResendOrganizationInvitation: jest.fn(),
}))

const list = jest.mocked(adminListOrganizationInvitations)
const resend = jest.mocked(adminResendOrganizationInvitation)
const previousDelivery = "2026-01-01T10:00:00Z"

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  return renderHook(({ open }) => useAdminOrgInvitations("org-test", open), {
    wrapper,
    initialProps: { open: true },
  })
}

async function advance(ms: number) {
  await act(async () => {
    await jest.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  jest.useFakeTimers()
  jest.clearAllMocks()
  list.mockResolvedValue({
    items: [{ id: "invite-test", last_emailed_at: previousDelivery }],
    has_more: false,
  } as Awaited<ReturnType<typeof adminListOrganizationInvitations>>)
  resend.mockResolvedValue(
    {} as Awaited<ReturnType<typeof adminResendOrganizationInvitation>>
  )
})

afterEach(() => jest.useRealTimers())

it("refreshes the delivery timestamp after the asynchronous send", async () => {
  const { result } = setup()
  await advance(1)
  await act(async () => {
    await result.current.resendInvitation("invite-test")
  })
  await advance(1)
  const newDelivery = "2026-01-01T10:05:00Z"
  list.mockResolvedValue({
    items: [{ id: "invite-test", last_emailed_at: newDelivery }],
    has_more: false,
  } as Awaited<ReturnType<typeof adminListOrganizationInvitations>>)
  await advance(2_100)
  expect(result.current.invitations[0].last_emailed_at).toBe(newDelivery)
})

it("stops polling after 60 seconds when delivery never completes", async () => {
  const { result } = setup()
  await advance(1)
  await act(async () => {
    await result.current.resendInvitation("invite-test")
  })
  await advance(61_000)
  expect(list.mock.calls.length).toBeGreaterThan(2)
  const calls = list.mock.calls.length
  await advance(60_000)
  expect(list).toHaveBeenCalledTimes(calls)
  expect(result.current.invitations[0].last_emailed_at).toBe(previousDelivery)
})

it("does not poll while the dialog is closed", async () => {
  const { result, rerender } = setup()
  await advance(1)
  await act(async () => {
    await result.current.resendInvitation("invite-test")
  })
  await advance(1)
  rerender({ open: false })
  const calls = list.mock.calls.length
  await advance(10_000)
  expect(list).toHaveBeenCalledTimes(calls)
})
