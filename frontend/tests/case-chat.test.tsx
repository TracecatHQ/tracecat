import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { CaseChat } from "@/components/cases/case-chat"
import { useCaseChatSession } from "@/hooks/use-case-chat-session"

const caseId = "case-test"
const pathname = `/workspaces/workspace-test/cases/${caseId}`
const mockReplace = jest.fn()
let currentSearchParams = new URLSearchParams()

jest.mock("next/navigation", () => ({
  usePathname: () => pathname,
  useRouter: () => ({ replace: mockReplace }),
  useSearchParams: () => currentSearchParams,
}))

jest.mock("@/components/chat/chat-interface", () => ({
  ChatInterface: ({
    chatId,
    onChatSelect,
  }: {
    chatId?: string
    onChatSelect?: (chatId: string) => void
  }) => (
    <div>
      <div>Selected session: {chatId ?? "none"}</div>
      <button type="button" onClick={() => onChatSelect?.("next-session")}>
        Select another session
      </button>
    </div>
  ),
}))

function TestCaseChat() {
  const { openChatSession } = useCaseChatSession()

  return (
    <>
      <button
        type="button"
        onClick={() => openChatSession("invocation-session")}
      >
        View invocation session
      </button>
      <CaseChat caseId={caseId} />
    </>
  )
}

describe("case chat session navigation", () => {
  beforeEach(() => {
    currentSearchParams = new URLSearchParams("tab=comments&view=expanded")
    localStorage.clear()
    mockReplace.mockReset()
    mockReplace.mockImplementation((href: string) => {
      const url = new URL(href, "http://localhost")
      currentSearchParams = new URLSearchParams(url.search)
    })
  })

  it("opens a requested session and keeps later CaseChat selections in sync", async () => {
    const user = userEvent.setup()
    const { rerender } = render(<TestCaseChat />)

    expect(screen.getByText("Selected session: none")).toBeInTheDocument()

    await user.click(
      screen.getByRole("button", { name: "View invocation session" })
    )

    expect(mockReplace).toHaveBeenLastCalledWith(
      `${pathname}?tab=comments&view=expanded&chatId=invocation-session`,
      { scroll: false }
    )
    expect(localStorage.getItem("workspace_chat_open")).toBe("true")

    rerender(<TestCaseChat />)
    expect(
      screen.getByText("Selected session: invocation-session")
    ).toBeInTheDocument()

    await user.click(
      screen.getByRole("button", { name: "Select another session" })
    )

    expect(mockReplace).toHaveBeenLastCalledWith(
      `${pathname}?tab=comments&view=expanded&chatId=next-session`,
      { scroll: false }
    )

    rerender(<TestCaseChat />)
    expect(
      screen.getByText("Selected session: next-session")
    ).toBeInTheDocument()
  })
})
