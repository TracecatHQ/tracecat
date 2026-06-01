import { act, render, screen } from "@testing-library/react"
import type { ReactNode } from "react"
import { SmoothResponse } from "@/components/chat/smooth-response"

jest.mock("@/components/ai-elements/response", () => ({
  Response: ({ children }: { children: ReactNode }) => (
    <div data-testid="response">{children}</div>
  ),
}))

describe("SmoothResponse", () => {
  const originalCancelAnimationFrame = globalThis.cancelAnimationFrame
  const originalRequestAnimationFrame = globalThis.requestAnimationFrame
  const originalWindowCancelAnimationFrame = window.cancelAnimationFrame
  const originalWindowRequestAnimationFrame = window.requestAnimationFrame
  let frameCallbacks: Map<number, FrameRequestCallback>
  let nextFrameId: number
  let cancelAnimationFrameMock: jest.Mock<void, [number]>
  let requestAnimationFrameMock: jest.Mock<number, [FrameRequestCallback]>

  beforeEach(() => {
    frameCallbacks = new Map()
    nextFrameId = 1
    jest.spyOn(performance, "now").mockReturnValue(0)
    cancelAnimationFrameMock = jest.fn((frameId: number) => {
      frameCallbacks.delete(frameId)
    })
    requestAnimationFrameMock = jest.fn((callback: FrameRequestCallback) => {
      const frameId = nextFrameId
      nextFrameId += 1
      frameCallbacks.set(frameId, callback)
      return frameId
    })

    Object.defineProperty(globalThis, "cancelAnimationFrame", {
      configurable: true,
      value: cancelAnimationFrameMock,
    })
    Object.defineProperty(globalThis, "requestAnimationFrame", {
      configurable: true,
      value: requestAnimationFrameMock,
    })
    Object.defineProperty(window, "cancelAnimationFrame", {
      configurable: true,
      value: cancelAnimationFrameMock,
    })
    Object.defineProperty(window, "requestAnimationFrame", {
      configurable: true,
      value: requestAnimationFrameMock,
    })
  })

  afterEach(() => {
    jest.restoreAllMocks()
    Object.defineProperty(globalThis, "cancelAnimationFrame", {
      configurable: true,
      value: originalCancelAnimationFrame,
    })
    Object.defineProperty(globalThis, "requestAnimationFrame", {
      configurable: true,
      value: originalRequestAnimationFrame,
    })
    Object.defineProperty(window, "cancelAnimationFrame", {
      configurable: true,
      value: originalWindowCancelAnimationFrame,
    })
    Object.defineProperty(window, "requestAnimationFrame", {
      configurable: true,
      value: originalWindowRequestAnimationFrame,
    })
  })

  function runFrame(frameId: number, now: number) {
    const callback = frameCallbacks.get(frameId)
    expect(callback).toBeDefined()
    frameCallbacks.delete(frameId)
    act(() => {
      callback?.(now)
    })
  }

  it("stops the reveal loop when caught up and restarts when text grows", () => {
    const { rerender } = render(<SmoothResponse text="Hello" animate={true} />)

    expect(screen.getByTestId("response")).toHaveTextContent("Hello")
    expect(requestAnimationFrameMock).not.toHaveBeenCalled()

    rerender(<SmoothResponse text="Hello world" animate={true} />)

    expect(screen.getByTestId("response")).toHaveTextContent("Hello")
    expect(requestAnimationFrameMock).toHaveBeenCalledTimes(1)

    runFrame(1, 100)

    expect(screen.getByTestId("response")).toHaveTextContent("Hello world")
    expect(requestAnimationFrameMock).toHaveBeenCalledTimes(1)

    rerender(<SmoothResponse text="Hello world!" animate={true} />)

    expect(requestAnimationFrameMock).toHaveBeenCalledTimes(2)
  })
})
