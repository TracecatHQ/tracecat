/**
 * @jest-environment jsdom
 */

import { fireEvent, render, screen } from "@testing-library/react"
import type { ReactNode } from "react"
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
// the view's switching and gating behavior.
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

jest.mock("@/components/locked-feature-modal", () => ({
  LockedFeatureModal: ({
    open,
    title,
    description,
  }: {
    open?: boolean
    title?: string
    description?: ReactNode
  }) =>
    open ? (
      <div role="dialog">
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
    ) : null,
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

describe("CasePanelView entitlement-locked tabs", () => {
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

  it("renders the Tasks tab without case_addons and opens the dialog on press", () => {
    mockEntitlements([])
    renderView()

    const tasksTab = screen.getByRole("tab", { name: "Tasks" })
    fireEvent.click(tasksTab)

    expect(screen.getByRole("dialog")).toBeInTheDocument()
    expect(screen.getByText("Enterprise only")).toBeInTheDocument()
    expect(
      screen.getByText("Case tasks are only available on enterprise plans.")
    ).toBeInTheDocument()
    // Blocked press: no navigation, panel stays on the default.
    expect(mockReplace).not.toHaveBeenCalled()
    expect(screen.getByTestId("panel-content")).toHaveTextContent("description")
  })

  it("resolves a ?tab=tasks deep link to the default panel without a dialog", () => {
    mockEntitlements([])
    searchParamsValue = new URLSearchParams("tab=tasks")
    renderView()

    expect(screen.getByTestId("panel-content")).toHaveTextContent("description")
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("opens the dialog from the digit shortcut of a locked panel", () => {
    mockEntitlements([])
    renderView()

    fireEvent.keyDown(document.body, { key: "2" })

    expect(screen.getByRole("dialog")).toBeInTheDocument()
    expect(mockReplace).not.toHaveBeenCalled()
  })

  it("treats a locked press as a no-op while entitlements load", () => {
    mockEntitlements([], true)
    renderView()

    fireEvent.click(screen.getByRole("tab", { name: "Tasks" }))

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    expect(mockReplace).not.toHaveBeenCalled()
  })

  it("switches to the Tasks panel with case_addons", () => {
    mockEntitlements(["case_addons"])
    renderView()

    fireEvent.click(screen.getByRole("tab", { name: "Tasks" }))

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    expect(mockReplace).toHaveBeenCalledWith(
      "/workspaces/ws-1/cases/case-1?tab=tasks",
      { scroll: false }
    )
  })
})
