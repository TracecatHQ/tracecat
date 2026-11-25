import { act, renderHook } from "@testing-library/react"
import type { ReactNode } from "react"
import { useVercelChat } from "@/hooks/use-chat"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/lib/api", () => ({ getBaseUrl: () => "https://example.invalid" }))
jest.mock("@/components/ui/use-toast", () => ({ toast: jest.fn() }))

// jsdom lacks two globals the SDK path needs.
global.Response ??= class {} as unknown as typeof Response
global.structuredClone ??= <T,>(value: T): T =>
  JSON.parse(JSON.stringify(value))

it("resends a failed first send's Slack destination on Retry", async () => {
  const fetchMock = jest.fn().mockRejectedValue(new Error("network down"))
  global.fetch = fetchMock as unknown as typeof fetch
  jest.spyOn(console, "error").mockImplementation(() => {})
  const { result } = renderHook(
    () =>
      useVercelChat({
        chatId: "chat-1",
        workspaceId: "workspace-1",
        messages: [],
        resume: false,
      }),
    {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={new QueryClient()}>
          {children}
        </QueryClientProvider>
      ),
    }
  )
  const destination = { kind: "channel", id: "C1" }
  function sentDestination() {
    const calls = fetchMock.mock.calls
    return JSON.parse(calls[calls.length - 1][1].body).channel_destination
  }

  await act(() =>
    result.current.sendMessage(
      { text: "Hello" },
      { body: { channel_destination: destination } }
    )
  )
  expect(sentDestination()).toEqual(destination)
  const promptId = result.current.messages[0].id
  await act(() => result.current.regenerate({ messageId: promptId }))
  expect(sentDestination()).toEqual(destination)

  // A later prompt sent without one doesn't inherit it.
  await act(() => result.current.sendMessage({ text: "Again" }))
  expect(sentDestination()).toBeUndefined()
  const nextId = result.current.messages[result.current.messages.length - 1].id
  await act(() => result.current.regenerate({ messageId: nextId }))
  expect(sentDestination()).toBeUndefined()
  jest.restoreAllMocks()
})

it("drops a refused destination so a Retry can run", async () => {
  const fetchMock = jest.fn().mockResolvedValue({
    ok: false,
    status: 422,
    text: async () =>
      JSON.stringify({
        detail: {
          type: "ChannelDestinationRefused",
          message: "Couldn't open Slack thread: bot paused",
        },
      }),
  })
  global.fetch = fetchMock as unknown as typeof fetch
  jest.spyOn(console, "error").mockImplementation(() => {})
  const { result } = renderHook(
    () =>
      useVercelChat({
        chatId: "chat-1",
        workspaceId: "workspace-1",
        messages: [],
        resume: false,
      }),
    {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={new QueryClient()}>
          {children}
        </QueryClientProvider>
      ),
    }
  )

  await act(() =>
    result.current.sendMessage(
      { text: "Hello" },
      { body: { channel_destination: { kind: "channel", id: "C1" } } }
    )
  )
  const promptId = result.current.messages[0].id
  await act(() => result.current.regenerate({ messageId: promptId }))
  const calls = fetchMock.mock.calls
  expect(
    JSON.parse(calls[calls.length - 1][1].body).channel_destination
  ).toBeUndefined()
  jest.restoreAllMocks()
})
