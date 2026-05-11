/**
 * @jest-environment jsdom
 */

import { render } from "@testing-library/react"
import CasesPage from "@/app/workspaces/[workspaceId]/cases/page"

const mockUseCaseColumnVisibility = jest.fn()
const mockUseCases = jest.fn()

let mockEntitlementsLoaded = true
let mockCaseAddonsEnabled = true
let mockDropdownDefinitions:
  | Array<{
      ref: string
    }>
  | undefined
let mockDropdownDefinitionsIsFetching = false
let mockCaseFields:
  | Array<{
      id: string
      reserved: boolean
    }>
  | undefined
let mockCaseFieldsIsFetching = false
let mockCaseDurationDefinitions:
  | Array<{
      id: string
    }>
  | undefined
let mockCaseDurationDefinitionsIsFetching = false

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: (entitlement: string) =>
      entitlement === "case_addons" ? mockCaseAddonsEnabled : false,
    hasEntitlementData: mockEntitlementsLoaded,
    isLoading: !mockEntitlementsLoaded,
  }),
}))

jest.mock("@/hooks/use-workspace", () => ({
  useWorkspaceMembers: () => ({
    members: [],
  }),
}))

jest.mock("@/hooks/use-case-column-visibility", () => ({
  useCaseColumnVisibility: (...args: unknown[]) =>
    mockUseCaseColumnVisibility(...args),
}))

jest.mock("@/hooks/use-cases", () => ({
  useCases: (...args: unknown[]) => mockUseCases(...args),
}))

jest.mock("@/lib/hooks", () => ({
  useCaseDropdownDefinitions: () => ({
    dropdownDefinitions: mockDropdownDefinitions,
    dropdownDefinitionsIsFetching: mockDropdownDefinitionsIsFetching,
  }),
  useCaseDurationDefinitions: () => ({
    caseDurationDefinitions: mockCaseDurationDefinitions,
    caseDurationDefinitionsIsFetching: mockCaseDurationDefinitionsIsFetching,
  }),
  useCaseFields: () => ({
    caseFields: mockCaseFields,
    caseFieldsIsFetching: mockCaseFieldsIsFetching,
  }),
  useCaseTagCatalog: () => ({
    caseTags: [],
  }),
}))

jest.mock("@/components/cases/cases-layout", () => ({
  CasesLayout: () => null,
}))

describe("CasesPage", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockEntitlementsLoaded = true
    mockCaseAddonsEnabled = true
    mockDropdownDefinitions = undefined
    mockDropdownDefinitionsIsFetching = false
    mockCaseFields = undefined
    mockCaseFieldsIsFetching = false
    mockCaseDurationDefinitions = undefined
    mockCaseDurationDefinitionsIsFetching = false

    mockUseCaseColumnVisibility.mockReturnValue({
      visibleColumnIds: [],
      toggleColumn: jest.fn(),
    })
    mockUseCases.mockReturnValue({
      cases: [],
      isLoading: false,
      error: null,
      filters: {},
      refetch: jest.fn(),
      setSearchQuery: jest.fn(),
      setSortBy: jest.fn(),
      setStatusFilter: jest.fn(),
      setStatusMode: jest.fn(),
      setPriorityFilter: jest.fn(),
      setPriorityMode: jest.fn(),
      setPrioritySortDirection: jest.fn(),
      setSeverityFilter: jest.fn(),
      setSeverityMode: jest.fn(),
      setSeveritySortDirection: jest.fn(),
      setAssigneeFilter: jest.fn(),
      setAssigneeMode: jest.fn(),
      setAssigneeSortDirection: jest.fn(),
      setTagFilter: jest.fn(),
      setTagMode: jest.fn(),
      setTagSortDirection: jest.fn(),
      setDropdownFilter: jest.fn(),
      setDropdownMode: jest.fn(),
      setDropdownSortDirection: jest.fn(),
      setUpdatedAfter: jest.fn(),
      setCreatedAfter: jest.fn(),
      totalFilteredCaseEstimate: 0,
      stageCounts: undefined,
      isCountsLoading: false,
      isCountsFetching: false,
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: jest.fn(),
    })
  })

  it("preserves stored columns until entitlement data and definitions resolve", () => {
    mockEntitlementsLoaded = false
    mockCaseAddonsEnabled = false
    mockCaseFields = [{ id: "priority_reason", reserved: false }]
    mockDropdownDefinitions = [{ ref: "region" }]
    mockCaseDurationDefinitions = [{ id: "duration-1" }]

    const { rerender } = render(<CasesPage />)

    expect(mockUseCaseColumnVisibility).toHaveBeenLastCalledWith(
      "workspace-1",
      undefined
    )

    mockEntitlementsLoaded = true
    mockCaseAddonsEnabled = true

    rerender(<CasesPage />)

    expect(mockUseCaseColumnVisibility).toHaveBeenLastCalledWith(
      "workspace-1",
      new Set([
        "dropdown:region",
        "field:priority_reason",
        "duration:duration-1",
      ])
    )
  })

  it("waits for fresh metadata before pruning cached column selections", () => {
    mockCaseAddonsEnabled = true
    mockEntitlementsLoaded = true
    mockCaseFields = [{ id: "stale_field", reserved: false }]
    mockCaseFieldsIsFetching = true
    mockDropdownDefinitions = [{ ref: "stale-region" }]
    mockDropdownDefinitionsIsFetching = true
    mockCaseDurationDefinitions = [{ id: "stale-duration" }]
    mockCaseDurationDefinitionsIsFetching = true

    const { rerender } = render(<CasesPage />)

    expect(mockUseCaseColumnVisibility).toHaveBeenLastCalledWith(
      "workspace-1",
      undefined
    )

    mockCaseFields = [{ id: "fresh_field", reserved: false }]
    mockCaseFieldsIsFetching = false
    mockDropdownDefinitions = [{ ref: "fresh-region" }]
    mockDropdownDefinitionsIsFetching = false
    mockCaseDurationDefinitions = [{ id: "fresh-duration" }]
    mockCaseDurationDefinitionsIsFetching = false

    rerender(<CasesPage />)

    expect(mockUseCaseColumnVisibility).toHaveBeenLastCalledWith(
      "workspace-1",
      new Set([
        "dropdown:fresh-region",
        "field:fresh_field",
        "duration:fresh-duration",
      ])
    )
  })
})
