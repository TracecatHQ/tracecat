/**
 * @jest-environment jsdom
 */

import { render } from "@testing-library/react"
import InboxPage from "@/app/workspaces/[workspaceId]/inbox/page"

const CASE_ID = "11111111-1111-4111-8111-111111111111"
const mockUseInbox = jest.fn()
let mockCaseAddonsEnabled = false
let mockEntitlementsLoading = false

jest.mock("next/navigation", () => ({
  usePathname: () => "/workspaces/workspace-test/inbox",
  useRouter: () => ({ replace: jest.fn() }),
  useSearchParams: () => new URLSearchParams({ caseId: CASE_ID }),
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => true,
}))

jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: (key: string) =>
      key === "case_addons" && mockCaseAddonsEnabled,
    isLoading: mockEntitlementsLoading,
  }),
}))

jest.mock("@/hooks/use-inbox", () => ({
  useInbox: (options: unknown) => {
    mockUseInbox(options)
    return { groups: {}, sessions: [], filters: {} }
  },
}))

jest.mock("@/components/inbox", () => ({
  ActivityLayout: ({ caseId }: { caseId: string | null }) => (
    <div data-testid="case-filter">{caseId ?? "none"}</div>
  ),
}))

describe("Inbox page case filter entitlement", () => {
  beforeEach(() => {
    mockUseInbox.mockClear()
    mockCaseAddonsEnabled = false
    mockEntitlementsLoading = false
  })

  it("ignores the case filter without case add-ons", () => {
    const { getByTestId } = render(<InboxPage />)

    expect(mockUseInbox).toHaveBeenLastCalledWith(
      expect.objectContaining({ caseId: null, enabled: true })
    )
    expect(getByTestId("case-filter")).toHaveTextContent("none")
  })

  it("applies the case filter with case add-ons", () => {
    mockCaseAddonsEnabled = true
    const { getByTestId } = render(<InboxPage />)

    expect(mockUseInbox).toHaveBeenLastCalledWith(
      expect.objectContaining({ caseId: CASE_ID, enabled: true })
    )
    expect(getByTestId("case-filter")).toHaveTextContent(CASE_ID)
  })

  it("waits for entitlements before fetching a case-filtered inbox", () => {
    mockEntitlementsLoading = true
    render(<InboxPage />)

    expect(mockUseInbox).toHaveBeenLastCalledWith(
      expect.objectContaining({ enabled: false })
    )
  })
})
