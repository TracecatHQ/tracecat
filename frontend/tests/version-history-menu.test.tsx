import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { TooltipProvider } from "@/components/ui/tooltip"
import type {
  VersionFileEntry,
  VersionHistoryEntry,
} from "@/components/version-history/types"
import { VersionDiffBody } from "@/components/version-history/version-diff-body"
import type { VersionHistoryMenuProps } from "@/components/version-history/version-history-menu"
import { VersionHistoryMenu } from "@/components/version-history/version-history-menu"

// InlineDiffView is developed in parallel; the mock keeps this suite runnable
// while the real module path stays imported (and type-checked) by
// VersionDiffBody. `virtual` lets the mock stand in before the file lands.
jest.mock(
  "@/components/diff/inline-diff-view",
  () => ({
    InlineDiffView: ({ path }: { path: string }) => (
      <div data-testid="inline-diff-view">{path}</div>
    ),
  }),
  { virtual: true }
)

beforeAll(() => {
  if (!HTMLElement.prototype.hasPointerCapture) {
    Object.defineProperty(HTMLElement.prototype, "hasPointerCapture", {
      value: () => false,
    })
  }
  if (!HTMLElement.prototype.setPointerCapture) {
    Object.defineProperty(HTMLElement.prototype, "setPointerCapture", {
      value: () => undefined,
    })
  }
  if (!HTMLElement.prototype.releasePointerCapture) {
    Object.defineProperty(HTMLElement.prototype, "releasePointerCapture", {
      value: () => undefined,
    })
  }
})

const VERSIONS: VersionHistoryEntry[] = [
  {
    id: "ver-2",
    label: "v2",
    description: "2 files",
    createdAt: "2026-08-01T00:00:00Z",
    isCurrent: true,
  },
  {
    id: "ver-1",
    label: "v1",
    createdAt: "2026-07-01T00:00:00Z",
  },
]

function renderMenu(overrides: Partial<VersionHistoryMenuProps> = {}) {
  const props: VersionHistoryMenuProps = {
    document: {
      entityLabel: "agent",
      name: "Triage agent",
      currentVersionId: "ver-2",
    },
    entityLabel: "agent",
    versions: VERSIONS,
    isLoading: false,
    onRestore: jest.fn().mockResolvedValue(undefined),
    renderVersionDiff: jest.fn((versionId: string) => (
      <div data-testid="diff-body">{versionId}</div>
    )),
    ...overrides,
  }
  render(
    <TooltipProvider>
      <VersionHistoryMenu {...props} />
    </TooltipProvider>
  )
  return props
}

async function openDropdown(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Versions" }))
}

describe("VersionHistoryMenu", () => {
  it("shows a loading state while versions are being fetched", async () => {
    const user = userEvent.setup()
    renderMenu({ isLoading: true, versions: [] })

    await openDropdown(user)

    expect(screen.getByText("Loading versions…")).toBeInTheDocument()
  })

  it("shows an empty state when there are no versions", async () => {
    const user = userEvent.setup()
    renderMenu({ versions: [] })

    await openDropdown(user)

    expect(screen.getByText("No versions yet.")).toBeInTheDocument()
  })

  it("lists versions and marks the current one", async () => {
    const user = userEvent.setup()
    renderMenu()

    await openDropdown(user)

    expect(screen.getByText("v2")).toBeInTheDocument()
    expect(screen.getByText("v1")).toBeInTheDocument()
    expect(screen.getByText("2 files")).toBeInTheDocument()
    expect(screen.getByText("Current")).toBeInTheDocument()
  })

  it("invokes renderVersionDiff with the selected version id", async () => {
    const user = userEvent.setup()
    const props = renderMenu()

    await openDropdown(user)
    await user.click(screen.getByText("v1"))

    expect(screen.getByRole("alertdialog")).toBeInTheDocument()
    expect(props.renderVersionDiff).toHaveBeenCalledWith("ver-1")
    expect(screen.getByTestId("diff-body")).toHaveTextContent("ver-1")
    expect(
      screen.getByText(
        "Compare v1 with the current draft of Triage agent. Restoring replaces unsaved changes."
      )
    ).toBeInTheDocument()
  })

  it("closes the dialog when restore succeeds", async () => {
    const user = userEvent.setup()
    const props = renderMenu()

    await openDropdown(user)
    await user.click(screen.getByText("v1"))
    await user.click(screen.getByRole("button", { name: "Restore version" }))

    expect(props.onRestore).toHaveBeenCalledWith("ver-1")
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
    })
  })

  it("keeps the dialog open when restore is rejected", async () => {
    const user = userEvent.setup()
    const props = renderMenu({
      onRestore: jest.fn().mockRejectedValue(new Error("restore failed")),
    })

    await openDropdown(user)
    await user.click(screen.getByText("v1"))
    await user.click(screen.getByRole("button", { name: "Restore version" }))

    await waitFor(() => {
      expect(props.onRestore).toHaveBeenCalledWith("ver-1")
    })
    expect(screen.getByRole("alertdialog")).toBeInTheDocument()
  })

  it("cannot be dismissed while a restore is pending", async () => {
    const user = userEvent.setup()
    let resolveRestore: (() => void) | undefined
    const onRestore = jest.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveRestore = resolve
        })
    )
    renderMenu({ onRestore })

    await openDropdown(user)
    await user.click(screen.getByText("v1"))
    await user.click(screen.getByRole("button", { name: "Restore version" }))

    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled()

    await user.keyboard("{Escape}")
    expect(screen.getByRole("alertdialog")).toBeInTheDocument()

    resolveRestore?.()
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
    })
  })

  it("renders the diff body with file statuses and the inline diff", async () => {
    const user = userEvent.setup()
    const files: VersionFileEntry[] = [
      { path: "instructions.md", status: "modified" },
      { path: "scripts/run.py", status: "removed" },
      { path: "config.yaml", status: "unchanged" },
    ]
    renderMenu({
      renderVersionDiff: (versionId: string) => (
        <VersionDiffBody
          files={files}
          selectedPath="instructions.md"
          onSelectPath={() => {}}
          diff={{
            path: "instructions.md",
            oldValue: "draft text",
            newValue: "version text",
          }}
          draftLabel="Current draft"
          versionLabel={versionId === "ver-1" ? "v1" : versionId}
        />
      ),
    })

    await openDropdown(user)
    await user.click(screen.getByText("v1"))

    expect(screen.getByText("Current draft → v1")).toBeInTheDocument()
    expect(screen.getByTestId("inline-diff-view")).toHaveTextContent(
      "instructions.md"
    )
    expect(screen.getByText("Modified")).toBeInTheDocument()
    expect(screen.getByText("Removed")).toBeInTheDocument()
  })
})
