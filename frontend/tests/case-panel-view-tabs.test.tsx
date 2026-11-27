/**
 * @jest-environment jsdom
 */

import { fireEvent, render, screen } from "@testing-library/react"
import { CasePanelView } from "@/components/cases/case-panel-view"
import { useEntitlements } from "@/hooks/use-entitlements"
import {
  useCaseDropdownDefinitions,
  useCaseDurationDefinitions,
  useCaseDurations,
  useCaseFields,
  useCaseTasks,
  useGetCase,
  useSetCaseDropdownValue,
  useUpdateCase,
} from "@/lib/hooks"

const mockReplace = jest.fn()
let searchParamsValue = new URLSearchParams()

jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace }),
  useSearchParams: () => searchParamsValue,
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "ws-1",
}))

jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: jest.fn(),
}))

jest.mock("@/hooks/use-workspace", () => ({
  useWorkspaceMembers: () => ({ members: [] }),
}))

// Keep the details rail undocked so the view renders its narrow layout.
jest.mock("@/hooks/use-is-at-least-width", () => ({
  useIsAtLeastWidth: () => false,
}))

jest.mock("@/lib/hooks", () => ({
  useCaseDropdownDefinitions: jest.fn(),
  useCaseDurationDefinitions: jest.fn(),
  useCaseDurations: jest.fn(),
  useCaseFields: jest.fn(),
  useCaseTasks: jest.fn(),
  useGetCase: jest.fn(),
  useSetCaseDropdownValue: jest.fn(),
  useUpdateCase: jest.fn(),
}))

// The active panel's key is the only thing these tests assert about content.
jest.mock("@/components/cases/case-panel-content", () => ({
  CasePanelContent: ({ panel }: { panel: string }) => (
    <div data-testid="panel-content">{panel}</div>
  ),
}))

// Heavy children with their own test coverage; stubbed to keep this suite on
// the view's switching behavior.
jest.mock("@/components/cases/case-panel-summary", () => ({
  CasePanelSummary: () => <div />,
}))
jest.mock("@/components/cases/case-version-history", () => ({
  CaseVersionHistory: () => <div />,
}))
jest.mock("@/components/cases/case-comments-section", () => ({
  CommentSection: () => <div />,
}))
jest.mock("@/components/cases/case-panel-selectors", () => ({
  AssigneeSelect: () => <div />,
  CaseDropdownSelect: () => <div />,
  PrioritySelect: () => <div />,
  SeveritySelect: () => <div />,
  VerdictSelect: () => <div />,
  StatusSelect: () => <div />,
}))
jest.mock("@/components/cases/case-panel-fields-group", () => ({
  CasePanelFieldsGroup: () => <div />,
}))
jest.mock("@/components/cases/case-workflow-trigger", () => ({
  CaseWorkflowTrigger: () => <div />,
}))
jest.mock("@/components/cases/case-tag-picker", () => ({
  CaseTagPicker: () => <div />,
}))
jest.mock("@/components/cases/case-closure-dialog", () => ({
  CaseClosureDialog: () => <div />,
}))

const mockUseEntitlements = useEntitlements as jest.MockedFunction<
  typeof useEntitlements
>
const mockUseGetCase = useGetCase as jest.MockedFunction<typeof useGetCase>
const mockUseCaseTasks = useCaseTasks as jest.MockedFunction<
  typeof useCaseTasks
>
const mockUseCaseFields = useCaseFields as jest.MockedFunction<
  typeof useCaseFields
>
const mockUseCaseDropdownDefinitions =
  useCaseDropdownDefinitions as jest.MockedFunction<
    typeof useCaseDropdownDefinitions
  >
const mockUseUpdateCase = useUpdateCase as jest.MockedFunction<
  typeof useUpdateCase
>
const mockUseSetCaseDropdownValue =
  useSetCaseDropdownValue as jest.MockedFunction<typeof useSetCaseDropdownValue>
const mockUseCaseDurations = useCaseDurations as jest.MockedFunction<
  typeof useCaseDurations
>
const mockUseCaseDurationDefinitions =
  useCaseDurationDefinitions as jest.MockedFunction<
    typeof useCaseDurationDefinitions
  >

function mockEntitlements(keys: string[], isLoading = false) {
  mockUseEntitlements.mockReturnValue({
    hasEntitlement: jest.fn().mockImplementation((key) => keys.includes(key)),
    isLoading,
    hasEntitlementData: !isLoading,
  })
}

const caseData = {
  id: "case-1",
  short_id: "CASE-0001",
  summary: "A case",
  status: "new",
  priority: "medium",
  severity: "low",
  assignee: null,
  fields: [],
  dropdown_values: [],
  tags: [],
  payload: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
} as unknown as NonNullable<ReturnType<typeof useGetCase>["caseData"]>

function renderView() {
  render(<CasePanelView caseId="case-1" />)
}

describe("CasePanelView tabs", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    searchParamsValue = new URLSearchParams()
    mockUseGetCase.mockReturnValue({
      caseData,
      caseDataIsLoading: false,
      caseDataError: null,
    } as unknown as ReturnType<typeof useGetCase>)
    mockUseCaseTasks.mockReturnValue({
      caseTasks: undefined,
    } as unknown as ReturnType<typeof useCaseTasks>)
    mockUseCaseFields.mockReturnValue({
      caseFields: [],
    } as unknown as ReturnType<typeof useCaseFields>)
    mockUseCaseDropdownDefinitions.mockReturnValue({
      dropdownDefinitions: [],
    } as unknown as ReturnType<typeof useCaseDropdownDefinitions>)
    mockUseUpdateCase.mockReturnValue({
      updateCase: jest.fn(),
    } as unknown as ReturnType<typeof useUpdateCase>)
    mockUseSetCaseDropdownValue.mockReturnValue(
      jest.fn() as unknown as ReturnType<typeof useSetCaseDropdownValue>
    )
    mockUseCaseDurations.mockReturnValue(
      {} as unknown as ReturnType<typeof useCaseDurations>
    )
    mockUseCaseDurationDefinitions.mockReturnValue(
      {} as unknown as ReturnType<typeof useCaseDurationDefinitions>
    )
  })

  it.each([
    ["Tasks", "tasks"],
    ["Sub-cases", "sub-cases"],
  ])("switches to the %s panel without case_addons", (name, panel) => {
    mockEntitlements([])
    renderView()

    fireEvent.click(screen.getByRole("tab", { name }))

    expect(mockReplace).toHaveBeenCalledWith(
      `/workspaces/ws-1/cases/case-1?tab=${panel}`,
      { scroll: false }
    )
  })

  it.each(["tasks", "sub-cases"])(
    "opens a ?tab=%s deep link without case_addons",
    (panel) => {
      mockEntitlements([])
      searchParamsValue = new URLSearchParams(`tab=${panel}`)
      renderView()

      expect(screen.getByTestId("panel-content")).toHaveTextContent(panel)
    }
  )

  it("switches panels from the digit shortcut without case_addons", () => {
    mockEntitlements([])
    renderView()

    fireEvent.keyDown(document.body, { key: "2" })

    expect(mockReplace).toHaveBeenCalledWith(
      "/workspaces/ws-1/cases/case-1?tab=tasks",
      { scroll: false }
    )
  })

  it.each([
    ["Tasks", "tasks"],
    ["Sub-cases", "sub-cases"],
  ])("keeps the %s panel open when embedded without a tab", (name, panel) => {
    mockEntitlements([])
    render(<CasePanelView caseId="case-1" embedded />)

    fireEvent.click(screen.getByRole("tab", { name }))

    expect(screen.getByTestId("panel-content")).toHaveTextContent(panel)
    expect(mockReplace).not.toHaveBeenCalled()
  })

  it("loads case tasks without case_addons", () => {
    mockEntitlements([])
    renderView()

    expect(mockUseCaseTasks).toHaveBeenCalledWith({
      caseId: "case-1",
      workspaceId: "ws-1",
    })
  })
})
