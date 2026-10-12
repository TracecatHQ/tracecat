import { useChat } from "@ai-sdk/react"
import { renderHook } from "@testing-library/react"
import type { UIMessage } from "ai"
import type { ReactNode } from "react"
import type { ChatPageContext } from "@/client"
import { useVercelChat } from "@/hooks/use-chat"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@ai-sdk/react", () => ({
  useChat: jest.fn(() => ({ status: "ready", messages: [] })),
}))

jest.mock("@/lib/api", () => ({ getBaseUrl: () => "https://example.invalid" }))

type PrepareSend = (options: { messages: UIMessage[] }) => {
  body: Record<string, unknown>
}

const USER_MESSAGE: UIMessage = {
  id: "message-1",
  role: "user",
  parts: [{ type: "text", text: "Make the model field a card picker" }],
}

function wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={new QueryClient()}>
      {children}
    </QueryClientProvider>
  )
}

function sentBody(pageContext?: ChatPageContext | null) {
  renderHook(
    () =>
      useVercelChat({
        chatId: "chat-1",
        workspaceId: "workspace-1",
        messages: [],
        pageContext,
      }),
    { wrapper }
  )
  const options = jest.mocked(useChat).mock.calls.at(-1)?.[0]
  const transport =
    options && "transport" in options ? options.transport : undefined
  const { prepareSendMessagesRequest } = transport as unknown as {
    prepareSendMessagesRequest: PrepareSend
  }
  return prepareSendMessagesRequest({ messages: [USER_MESSAGE] }).body
}

describe("page context in useVercelChat", () => {
  beforeEach(() => {
    jest.mocked(useChat).mockClear()
  })

  it("sends the page context with a chat turn", () => {
    const body = sentBody({ kind: "form", id: "form-1" })

    expect(body.kind).toBe("vercel")
    expect(body.page_context).toEqual({ kind: "form", id: "form-1" })
  })

  it.each([undefined, null])("sends none when it is %p", (pageContext) => {
    expect(sentBody(pageContext)).not.toHaveProperty("page_context")
  })
})
