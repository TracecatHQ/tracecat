import { render } from "@testing-library/react"
import { Conversation } from "@/components/ai-elements/conversation"

const mockScrollToBottom = jest.fn()
const mockScrollElement = { scrollTop: 0, scrollHeight: 0 }
const mockScrollRef = { current: mockScrollElement }

jest.mock("use-stick-to-bottom", () => ({
  StickToBottom: ({ children }: { children: React.ReactNode }) => children,
  useStickToBottomContext: () => ({
    scrollRef: mockScrollRef,
    scrollToBottom: mockScrollToBottom,
  }),
}))

describe("Conversation scroll anchoring", () => {
  beforeEach(() => {
    mockScrollToBottom.mockClear()
    mockScrollElement.scrollTop = 0
    mockScrollElement.scrollHeight = 0
  })

  it("jumps to the end on mount and only again when the transcript changes", () => {
    mockScrollElement.scrollHeight = 900
    const { rerender } = render(<Conversation scrollKey="chat-1" />)

    expect(mockScrollElement.scrollTop).toBe(900)
    expect(mockScrollToBottom).toHaveBeenCalledTimes(1)
    expect(mockScrollToBottom).toHaveBeenCalledWith("instant")

    // Same transcript re-rendering (streaming, status changes) must not
    // touch the scroll position.
    mockScrollElement.scrollTop = 120
    rerender(<Conversation scrollKey="chat-1" />)
    expect(mockScrollElement.scrollTop).toBe(120)
    expect(mockScrollToBottom).toHaveBeenCalledTimes(1)

    mockScrollElement.scrollHeight = 2400
    rerender(<Conversation scrollKey="chat-2" />)
    expect(mockScrollElement.scrollTop).toBe(2400)
    expect(mockScrollToBottom).toHaveBeenCalledTimes(2)
  })
})
