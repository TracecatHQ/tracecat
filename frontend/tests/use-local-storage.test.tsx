/**
 * @jest-environment jsdom
 */

import { act, renderHook } from "@testing-library/react"
import { StrictMode, useState } from "react"
import { useLocalStorage } from "@/hooks/use-local-storage"

/**
 * Mirrors the case layout: a sibling state update lands in the same batch as
 * the persisted toggle, so React cannot eagerly resolve the toggle and has to
 * run the updater while rendering.
 */
function useToggleHarness() {
  const [pending, setPending] = useState(0)
  const [open, setOpen] = useLocalStorage("chat-open", false)
  return { open, pending, setOpen, setPending }
}

describe("useLocalStorage", () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it("propagates a functional toggle to other subscribers of the same key", () => {
    const hookA = renderHook(() => useLocalStorage("chat-open", false), {
      wrapper: StrictMode,
    })
    const hookB = renderHook(() => useLocalStorage("chat-open", false), {
      wrapper: StrictMode,
    })

    expect(hookA.result.current[0]).toBe(false)
    expect(hookB.result.current[0]).toBe(false)

    act(() => {
      hookA.result.current[1]((prev) => !prev)
    })

    expect(hookA.result.current[0]).toBe(true)
    expect(hookB.result.current[0]).toBe(true)
  })

  it("invokes a functional updater exactly once", () => {
    const hookA = renderHook(() => useToggleHarness(), { wrapper: StrictMode })
    renderHook(() => useLocalStorage("chat-open", false), {
      wrapper: StrictMode,
    })

    const toggle = jest.fn((prev: boolean) => !prev)

    act(() => {
      hookA.result.current.setPending(1)
      hookA.result.current.setOpen(toggle)
    })

    expect(toggle).toHaveBeenCalledTimes(1)
    expect(toggle).toHaveBeenCalledWith(false)
    expect(hookA.result.current.open).toBe(true)
  })

  it("persists eagerly instead of deferring the write into a render", () => {
    const hookA = renderHook(() => useToggleHarness(), { wrapper: StrictMode })

    act(() => {
      hookA.result.current.setPending(1)
      hookA.result.current.setOpen((prev) => !prev)
      // Still inside the batch: the write must already have happened, because
      // it does not belong to React's render phase.
      expect(window.localStorage.getItem("chat-open")).toBe("true")
    })

    expect(hookA.result.current.open).toBe(true)
  })

  it("persists the reported value under the prefixed key", () => {
    const { result } = renderHook(
      () => useLocalStorage("chat-open", false, "workspace-1"),
      { wrapper: StrictMode }
    )

    act(() => {
      result.current[1]((prev) => !prev)
    })

    expect(result.current[0]).toBe(true)
    expect(window.localStorage.getItem("workspace-1_chat-open")).toBe("true")
    expect(window.localStorage.getItem("chat-open")).toBeNull()
  })

  it("composes sequential functional updates", () => {
    const { result } = renderHook(() => useLocalStorage("counter", 0), {
      wrapper: StrictMode,
    })

    act(() => {
      result.current[1]((count) => count + 1)
      result.current[1]((count) => count + 1)
    })

    expect(result.current[0]).toBe(2)
    expect(window.localStorage.getItem("counter")).toBe("2")
  })
})
