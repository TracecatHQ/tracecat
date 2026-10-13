import { act, renderHook } from "@testing-library/react"
import type { ReactNode } from "react"
import type { ChatPageContext } from "@/client"
import { useVercelChat } from "@/hooks/use-chat"
import { QueryClient, QueryClientProvider } from "@/lib/query"

// `@ai-sdk/react` is deliberately NOT mocked: its `useChat` keeps the transport
// from the first render of a chat id, which is the behaviour under test.

jest.mock("@/lib/api", () => ({ getBaseUrl: () => "https://example.invalid" }))

jest.mock("@/components/ui/use-toast", () => ({ toast: jest.fn() }))

type HookProps = { pageContext?: ChatPageContext | null }

const fetchMock = jest.fn()
const originalFetch = global.fetch

// jsdom lacks two globals the real SDK path needs: `Response` (the hook's error
// parser does an `instanceof` on it) and `structuredClone` (message snapshots).
if (typeof global.Response === "undefined") {
  global.Response = class {} as unknown as typeof Response
}
if (typeof global.structuredClone === "undefined") {
  global.structuredClone = <T,>(value: T): T =>
    JSON.parse(JSON.stringify(value))
}

function wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={new QueryClient()}>
      {children}
    </QueryClientProvider>
  )
}

function renderChat(initialProps: HookProps) {
  return renderHook(
    ({ pageContext }: HookProps) =>
      useVercelChat({
        chatId: "chat-1",
        workspaceId: "workspace-1",
        messages: [],
        pageContext,
        resume: false,
      }),
    { initialProps, wrapper }
  )
}

/** Send one turn and return the request body the transport posted. */
async function sendTurn(
  chat: ReturnType<typeof renderChat>["result"]
): Promise<Record<string, unknown>> {
  fetchMock.mockClear()
  await act(async () => {
    await chat.current.sendMessage({ text: "Make the model field a card" })
  })
  expect(fetchMock).toHaveBeenCalledTimes(1)
  const init = fetchMock.mock.calls[0][1] as { body: string }
  return JSON.parse(init.body)
}

describe("page context in useVercelChat", () => {
  beforeEach(() => {
    // The turn itself is irrelevant here; only the request body matters.
    fetchMock.mockRejectedValue(new Error("network down"))
    global.fetch = fetchMock as unknown as typeof fetch
    jest.spyOn(console, "error").mockImplementation(() => {})
  })

  afterEach(() => {
    global.fetch = originalFetch
    jest.restoreAllMocks()
  })

  it("sends the page context with a chat turn", async () => {
    const { result } = renderChat({ pageContext: { kind: "form", id: "f-1" } })

    const body = await sendTurn(result)

    expect(body.kind).toBe("vercel")
    expect(body.page_context).toEqual({ kind: "form", id: "f-1" })
  })

  it.each([undefined, null])("sends none when it is %p", async (value) => {
    const { result } = renderChat({ pageContext: value })

    expect(await sendTurn(result)).not.toHaveProperty("page_context")
  })

  it("sends the context of the page the chat is on now", async () => {
    // Same chat id throughout: the docked chat survives in-app navigation.
    const { result, rerender } = renderChat({ pageContext: undefined })

    rerender({ pageContext: { kind: "form", id: "f-1" } })
    expect((await sendTurn(result)).page_context).toEqual({
      kind: "form",
      id: "f-1",
    })

    rerender({ pageContext: { kind: "form", id: "f-2" } })
    expect((await sendTurn(result)).page_context).toEqual({
      kind: "form",
      id: "f-2",
    })

    rerender({ pageContext: null })
    expect(await sendTurn(result)).not.toHaveProperty("page_context")
  })
})
