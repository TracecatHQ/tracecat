import { renderHook } from "@testing-library/react"
import type { UIMessage } from "ai"
import { useAdoptServerTranscript } from "@/hooks/use-chat"

function text(id: string, role: UIMessage["role"]): UIMessage {
  return { id, role, parts: [{ type: "text", text: id }] }
}

describe("useAdoptServerTranscript", () => {
  it("adopts saved history after an in-chat approval", () => {
    const submitted: UIMessage = {
      id: "continue",
      role: "user",
      parts: [
        {
          type: "data-continue",
          data: { kind: "continue", source: "chat", decisions: [] },
        },
      ],
    }
    const live = [
      text("prompt", "user"),
      text("call", "assistant"),
      submitted,
      text("answer", "assistant"),
    ]
    const saved = [
      text("prompt-db", "user"),
      text("call-db", "assistant"),
      text("answer-db", "assistant"),
    ]
    const setMessages = jest.fn()

    renderHook(() =>
      useAdoptServerTranscript({
        status: "ready",
        serverMessages: saved,
        liveMessages: live,
        setMessages,
      })
    )

    expect(setMessages).toHaveBeenCalledWith(saved)
  })
})
