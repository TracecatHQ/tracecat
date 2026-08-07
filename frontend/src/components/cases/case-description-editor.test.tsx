import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import type { AgentPresetReadMinimal } from "@/client"
import { CaseCommentViewer } from "@/components/cases/case-description-editor"
import { useAgentPresets } from "@/hooks/use-agent-presets"
import { WorkspaceIdProvider } from "@/providers/workspace-id"

jest.mock("@/hooks/use-agent-presets", () => ({
  useAgentPresets: jest.fn(),
}))

const mockUseAgentPresets = useAgentPresets as jest.MockedFunction<
  typeof useAgentPresets
>

const WORKSPACE_ID = "1f8d0f19-4b9c-4f7a-9c3f-2f4b6d8e0a12"
const PRESET_ID = "0f9d9f4c-1c2b-4f3a-9a1e-2b7c8d9e0f11"

const preset: AgentPresetReadMinimal = {
  id: PRESET_ID,
  workspace_id: WORKSPACE_ID,
  name: "Triage Agent",
  slug: "triage-agent",
  description: null,
  model_provider: "openai",
  model_name: "gpt-4o",
  created_at: "2024-01-01T00:00:00.000Z",
  updated_at: "2024-01-01T00:00:00.000Z",
}

function renderViewer(content: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <WorkspaceIdProvider workspaceId={WORKSPACE_ID}>
        <CaseCommentViewer content={content} workspaceId={WORKSPACE_ID} />
      </WorkspaceIdProvider>
    </QueryClientProvider>
  )
}

let consoleErrorSpy: jest.SpyInstance

beforeAll(() => {
  // jsdom does not implement matchMedia, which `useIsMobile` calls on mount.
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
      addListener: jest.fn(),
      removeListener: jest.fn(),
      dispatchEvent: jest.fn(),
    }),
  })
})

beforeEach(() => {
  mockUseAgentPresets.mockReturnValue({
    presets: [preset],
    presetsIsLoading: false,
    presetsError: null,
    refetchPresets: jest.fn(),
  } as unknown as ReturnType<typeof useAgentPresets>)
  consoleErrorSpy = jest.spyOn(console, "error").mockImplementation(() => {})
})

afterEach(() => {
  consoleErrorSpy.mockRestore()
  mockUseAgentPresets.mockReset()
})

describe("CaseCommentViewer mentions", () => {
  it("renders a valid agent mention as a chip showing the resolved name", async () => {
    renderViewer(`Please review [@Stale Label](mention://agent/${PRESET_ID}).`)

    const chip = await screen.findByTestId("mention-chip")
    expect(chip).toHaveTextContent("@Triage Agent")
    expect(chip).toHaveAttribute("data-state", "resolved")
    expect(screen.queryByText("@Stale Label")).not.toBeInTheDocument()
    expect(consoleErrorSpy).not.toHaveBeenCalled()
  })

  it("leaves a malformed mention href as a plain link", async () => {
    const { container } = renderViewer(
      "Ping [@Nobody](mention://agent/not-a-uuid) please."
    )

    await waitFor(() => {
      expect(container.querySelector("a")).not.toBeNull()
    })
    const anchor = container.querySelector("a")
    expect(anchor?.textContent).toBe("@Nobody")
    expect(screen.queryByTestId("mention-chip")).not.toBeInTheDocument()
    expect(consoleErrorSpy).not.toHaveBeenCalled()
  })

  it("leaves an unknown mention segment as a plain link", async () => {
    const { container } = renderViewer(
      `Ping [@Someone](mention://user/${PRESET_ID}) please.`
    )

    await waitFor(() => {
      expect(container.querySelector("a")).not.toBeNull()
    })
    expect(container.querySelector("a")?.textContent).toBe("@Someone")
    expect(screen.queryByTestId("mention-chip")).not.toBeInTheDocument()
    expect(consoleErrorSpy).not.toHaveBeenCalled()
  })

  it("does not affect ordinary links", async () => {
    const { container } = renderViewer(
      `See [Docs](https://example.test/docs) and [@Stale](mention://agent/${PRESET_ID}).`
    )

    await screen.findByTestId("mention-chip")
    const anchors = Array.from(container.querySelectorAll("a"))
    expect(anchors).toHaveLength(1)
    expect(anchors[0]).toHaveAttribute("href", "https://example.test/docs")
    expect(anchors[0]?.textContent).toBe("Docs")
    expect(consoleErrorSpy).not.toHaveBeenCalled()
  })
})
