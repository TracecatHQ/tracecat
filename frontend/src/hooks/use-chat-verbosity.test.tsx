import { act, renderHook } from "@testing-library/react"
import { useChatVerbosityPreference } from "@/hooks/use-chat-verbosity"

const KEY = "tracecat_agent-verbosity"

describe("useChatVerbosityPreference", () => {
  afterEach(() => window.localStorage.clear())

  it("defaults to the default level", () => {
    const { result } = renderHook(() => useChatVerbosityPreference())
    expect(result.current[0]).toBe("default")
  })

  it("persists the chosen level in this browser", () => {
    const { result } = renderHook(() => useChatVerbosityPreference())
    act(() => result.current[1]("verbose"))

    expect(result.current[0]).toBe("verbose")
    expect(window.localStorage.getItem(KEY)).toBe('"verbose"')
  })

  it("falls back to the default for an unknown stored level", () => {
    window.localStorage.setItem(KEY, '"milestones"')
    const { result } = renderHook(() => useChatVerbosityPreference())
    expect(result.current[0]).toBe("default")
  })
})
