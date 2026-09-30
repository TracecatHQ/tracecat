import { useChat } from "@ai-sdk/react"
import { act, renderHook } from "@testing-library/react"
import type { ReactNode } from "react"
import { useVercelChat } from "@/hooks/use-chat"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@ai-sdk/react", () => ({
  useChat: jest.fn(() => ({ status: "ready", messages: [] })),
}))

jest.mock("@/lib/api", () => ({ getBaseUrl: () => "https://example.invalid" }))

function wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={new QueryClient()}>
      {children}
    </QueryClientProvider>
  )
}

describe("child stream lifecycle in useVercelChat", () => {
  beforeEach(() => {
    jest.useFakeTimers()
    jest.mocked(useChat).mockClear()
  })

  afterEach(() => {
    jest.clearAllTimers()
    jest.useRealTimers()
  })

  it.each([
    { isAbort: false, isDisconnect: false, isError: false, completed: true },
    { isAbort: true, isDisconnect: false, isError: false, completed: false },
    { isAbort: false, isDisconnect: true, isError: true, completed: false },
    { isAbort: false, isDisconnect: false, isError: true, completed: false },
  ])("only completes readers after a normal finish: %p", (flags) => {
    const { result } = renderHook(
      () =>
        useVercelChat({
          chatId: "parent",
          workspaceId: "workspace-1",
          messages: [],
        }),
      { wrapper }
    )
    const complete = jest.spyOn(result.current.subagentStore, "complete")
    const options = jest.mocked(useChat).mock.calls.at(-1)?.[0]
    const onFinish =
      options && "onFinish" in options ? options.onFinish : undefined
    expect(onFinish).toBeDefined()
    act(() => {
      onFinish?.({
        message: { id: "parent-reply", role: "assistant", parts: [] },
        messages: [],
        ...flags,
      })
    })
    expect(complete).toHaveBeenCalledTimes(flags.completed ? 1 : 0)
  })

  it("disposes readers on chat changes and unmount", () => {
    const { result, rerender, unmount } = renderHook(
      ({ chatId }) =>
        useVercelChat({ chatId, workspaceId: "workspace-1", messages: [] }),
      { initialProps: { chatId: "parent-a" }, wrapper }
    )
    const firstStore = result.current.subagentStore
    const disposeFirst = jest.spyOn(firstStore, "dispose")
    rerender({ chatId: "parent-b" })
    expect(disposeFirst).toHaveBeenCalledTimes(1)
    expect(result.current.subagentStore).not.toBe(firstStore)

    const disposeSecond = jest.spyOn(result.current.subagentStore, "dispose")
    unmount()
    expect(disposeSecond).toHaveBeenCalledTimes(1)
  })
})
