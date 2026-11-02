import { act, renderHook } from "@testing-library/react"
import type { ReactNode } from "react"
import type { ChatPageContext } from "@/client"
import { useVercelChat } from "@/hooks/use-chat"
import { QueryClient, QueryClientProvider } from "@/lib/query"

// `@ai-sdk/react` is NOT mocked: its `useChat` keeps the transport from the
// first render of a chat id, which is the behaviour under test.
jest.mock("@/lib/api", () => ({ getBaseUrl: () => "https://example.invalid" }))
jest.mock("@/components/ui/use-toast", () => ({ toast: jest.fn() }))

// jsdom lacks two globals the SDK path needs.
global.Response ??= class {} as unknown as typeof Response
global.structuredClone ??= <T,>(value: T): T =>
  JSON.parse(JSON.stringify(value))

it("sends the page context of the page the docked chat is on now", async () => {
  const fetchMock = jest.fn().mockRejectedValue(new Error("network down"))
  global.fetch = fetchMock as unknown as typeof fetch
  jest.spyOn(console, "error").mockImplementation(() => {})
  const { result, rerender } = renderHook(
    ({ pageContext }: { pageContext: ChatPageContext | null }) =>
      useVercelChat({
        chatId: "chat-1",
        workspaceId: "workspace-1",
        messages: [],
        pageContext,
        resume: false,
      }),
    {
      initialProps: { pageContext: null as ChatPageContext | null },
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={new QueryClient()}>
          {children}
        </QueryClientProvider>
      ),
    }
  )
  async function sentContext() {
    fetchMock.mockClear()
    await act(() => result.current.sendMessage({ text: "Make it a card" }))
    return JSON.parse(fetchMock.mock.calls[0][1].body).page_context
  }

  // The same chat survives in-app navigation between pages.
  expect(await sentContext()).toBeUndefined()
  rerender({ pageContext: { kind: "form", id: "f-1" } })
  expect(await sentContext()).toEqual({ kind: "form", id: "f-1" })
  rerender({ pageContext: { kind: "form", id: "f-2" } })
  expect(await sentContext()).toEqual({ kind: "form", id: "f-2" })
  jest.restoreAllMocks()
})
