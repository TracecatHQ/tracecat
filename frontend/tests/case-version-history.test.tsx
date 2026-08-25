import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import {
  CancelablePromise,
  type CasesListCaseVersionsResponse,
  type CaseVersionCompareRead,
  casesCompareCaseVersion,
  casesListCaseVersions,
  casesRestoreCaseVersion,
} from "@/client"
import { CaseVersionHistory } from "@/components/cases/case-version-history"
import { TooltipProvider } from "@/components/ui/tooltip"
import { client as apiClient } from "@/lib/api"
import { QueryClient, QueryClientProvider } from "@/lib/query"
import { ScopeProvider } from "@/providers/scopes"
import { WorkspaceIdProvider } from "@/providers/workspace-id"

jest.mock("@/client", () => ({
  ...jest.requireActual("@/client"),
  casesCompareCaseVersion: jest.fn(),
  casesListCaseVersions: jest.fn(),
  casesRestoreCaseVersion: jest.fn(),
}))

const compareVersion = jest.mocked(casesCompareCaseVersion)
const listVersions = jest.mocked(casesListCaseVersions)
const restoreVersion = jest.mocked(casesRestoreCaseVersion)
const SCOPE = { workspaceId: "workspace-1", caseId: "case-1" } as const

function apiResponse<T>(value: T): CancelablePromise<T> {
  return new CancelablePromise((resolve) => resolve(value))
}

const TITLE_VERSIONS = [
  {
    id: "title-v3",
    field: "summary" as const,
    version: 3,
    actor: {
      id: "user-1",
      email: "analyst@example.com",
      first_name: "Avery",
      last_name: "Analyst",
    },
    created_at: "2026-08-24T12:00:00Z",
    is_latest: true,
  },
  {
    id: "title-v2",
    field: "summary" as const,
    version: 2,
    actor: null,
    created_at: "2026-08-24T11:00:00Z",
    is_latest: false,
  },
]

beforeAll(() => {
  for (const method of [
    "hasPointerCapture",
    "setPointerCapture",
    "releasePointerCapture",
  ] as const) {
    if (!HTMLElement.prototype[method]) {
      Object.defineProperty(HTMLElement.prototype, method, {
        value: () => false,
      })
    }
  }
})

function renderHistory() {
  jest.spyOn(apiClient, "get").mockResolvedValue({
    data: { scopes: ["case:read", "case:update"] },
  })
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  function Providers({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <WorkspaceIdProvider workspaceId={SCOPE.workspaceId}>
          <ScopeProvider>
            <TooltipProvider>{children}</TooltipProvider>
          </ScopeProvider>
        </WorkspaceIdProvider>
      </QueryClientProvider>
    )
  }
  render(<CaseVersionHistory {...SCOPE} caseLabel="CASE-0001" />, {
    wrapper: Providers,
  })
}

async function openHistory(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Versions" }))
}

describe("case version history and restore", () => {
  it("filters, paginates, compares, and restores a field version", async () => {
    listVersions.mockImplementation(({ field, cursor }) => {
      if (field !== "summary") {
        return apiResponse<CasesListCaseVersionsResponse>({
          items: [
            TITLE_VERSIONS[0],
            {
              id: "description-v2",
              field: "description",
              version: 2,
              actor: null,
              created_at: "2026-08-24T11:30:00Z",
              is_latest: true,
            },
            TITLE_VERSIONS[1],
          ],
          next_cursor: null,
          has_more: false,
        })
      }
      if (cursor === "next-title-page") {
        return apiResponse<CasesListCaseVersionsResponse>({
          items: [
            {
              id: "title-v1",
              field: "summary",
              version: 1,
              actor: null,
              created_at: "2026-08-24T10:00:00Z",
              is_latest: false,
            },
          ],
          next_cursor: null,
          has_more: false,
        })
      }
      return apiResponse<CasesListCaseVersionsResponse>({
        items: TITLE_VERSIONS,
        next_cursor: "next-title-page",
        has_more: true,
      })
    })
    compareVersion.mockImplementation(({ versionId }) => {
      const version = Number(versionId.at(-1))
      if (version === 1) {
        return apiResponse<CaseVersionCompareRead>({
          selected: {
            id: versionId,
            field: "summary",
            version,
            content: "Original title",
          },
          predecessor: null,
        })
      }
      return apiResponse<CaseVersionCompareRead>({
        selected: {
          id: versionId,
          field: "summary",
          version,
          content: version === 2 ? "Updated title" : "Current title",
        },
        predecessor: {
          id: `title-v${version - 1}`,
          field: "summary",
          version: version - 1,
          content: version === 2 ? "Original title" : "Updated title",
        },
      })
    })
    restoreVersion.mockResolvedValue({
      restored: true,
      case_id: SCOPE.caseId,
      restored_from_version_id: "title-v2",
      field: "summary",
    })
    const user = userEvent.setup()
    renderHistory()

    await waitFor(() => expect(listVersions).toHaveBeenCalledTimes(1))
    expect(listVersions).toHaveBeenCalledWith(
      expect.objectContaining({ ...SCOPE, limit: 50 })
    )
    expect(compareVersion).not.toHaveBeenCalled()

    await openHistory(user)
    expect(screen.getByText("Title v3")).toBeInTheDocument()
    expect(screen.getByText("Description v2")).toBeInTheDocument()
    expect(screen.getAllByText("Current")).toHaveLength(2)
    expect(screen.getByText(/Avery Analyst ·/)).toBeInTheDocument()

    await user.click(screen.getByRole("menuitemradio", { name: "Title" }))
    await waitFor(() =>
      expect(listVersions).toHaveBeenCalledWith(
        expect.objectContaining({ ...SCOPE, field: "summary", limit: 50 })
      )
    )
    await screen.findByText("Load more")
    expect(screen.queryByText("Description v2")).not.toBeInTheDocument()

    await user.click(screen.getByText("Load more"))
    await screen.findByText("Title v1")
    expect(listVersions).toHaveBeenCalledWith(
      expect.objectContaining({
        ...SCOPE,
        cursor: "next-title-page",
        limit: 50,
      })
    )

    await user.click(screen.getByText("Title v3"))
    expect(
      screen.getByRole("button", { name: "Restore version" })
    ).toBeDisabled()
    await user.click(screen.getByRole("button", { name: "Cancel" }))

    await openHistory(user)
    await user.click(screen.getByText("Title v1"))
    expect(await screen.findByText("Baseline")).toBeInTheDocument()
    expect(screen.getByText("Original title")).toBeInTheDocument()
    expect(screen.getByText(/unsaved title draft/)).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Cancel" }))

    await openHistory(user)
    await user.click(screen.getByText("Title v2"))
    expect(await screen.findByText("Title v1 → Title v2")).toBeInTheDocument()
    const diff = screen.getByTestId("prose-diff")
    const deletion = within(diff).getByText("Original")
    const addition = within(diff).getByText("Updated")
    expect(deletion.tagName).toBe("DEL")
    expect(deletion).toHaveClass("text-muted-foreground", "line-through")
    expect(addition.tagName).toBe("INS")
    expect(addition).toHaveClass("bg-[hsl(var(--diff-added-emphasis))]")

    await user.click(screen.getByRole("button", { name: "Restore version" }))
    await waitFor(() =>
      expect(restoreVersion).toHaveBeenCalledWith({
        ...SCOPE,
        versionId: "title-v2",
      })
    )
    await waitFor(() =>
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
    )
  })
})
