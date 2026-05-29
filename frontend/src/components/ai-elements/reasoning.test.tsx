import { fireEvent, render, screen } from "@testing-library/react"
import type { ReactNode } from "react"
import {
  Reasoning,
  ReasoningContent,
  ReasoningTrigger,
} from "@/components/ai-elements/reasoning"

// Isolate the reasoning scroll behavior from the markdown rendering pipeline.
jest.mock("./response", () => ({
  Response: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
}))

/**
 * jsdom has no layout engine, so scroll geometry is always 0 and `scrollTop`
 * writes are ignored. Install real read/write metrics on the element so the
 * stick-to-bottom math is observable.
 */
function installScrollMetrics(
  el: HTMLElement,
  { scrollHeight, clientHeight }: { scrollHeight: number; clientHeight: number }
) {
  let scrollTop = 0
  Object.defineProperty(el, "scrollTop", {
    configurable: true,
    get: () => scrollTop,
    set: (value: number) => {
      scrollTop = value
    },
  })
  Object.defineProperty(el, "scrollHeight", {
    configurable: true,
    get: () => scrollHeight,
  })
  Object.defineProperty(el, "clientHeight", {
    configurable: true,
    get: () => clientHeight,
  })
}

function getScrollContainer(container: HTMLElement): HTMLElement {
  const el = container.querySelector('[data-slot="reasoning-content"]')
  if (!(el instanceof HTMLElement)) {
    throw new Error("reasoning scroll container not found")
  }
  return el
}

function ReasoningHarness({
  isStreaming,
  text,
}: {
  isStreaming: boolean
  text: string
}) {
  return (
    <Reasoning isStreaming={isStreaming} open>
      <ReasoningTrigger />
      <ReasoningContent>{text}</ReasoningContent>
    </Reasoning>
  )
}

describe("ReasoningContent", () => {
  it("renders reasoning inside a bounded, scrollable container", () => {
    const { container } = render(
      <ReasoningHarness isStreaming text="thinking out loud" />
    )

    const scroll = getScrollContainer(container)
    expect(scroll).toHaveClass("max-h-60", "overflow-y-auto")
    expect(screen.getByText("thinking out loud")).toBeInTheDocument()
  })

  it("scrolls to the bottom as deltas stream in", () => {
    const { container, rerender } = render(
      <ReasoningHarness isStreaming text="delta 1" />
    )

    const scroll = getScrollContainer(container)
    installScrollMetrics(scroll, { scrollHeight: 1000, clientHeight: 100 })
    scroll.scrollTop = 0

    rerender(<ReasoningHarness isStreaming text="delta 1 delta 2 delta 3" />)

    expect(scroll.scrollTop).toBe(1000)
  })

  it("does not auto-scroll when it is not streaming", () => {
    const { container, rerender } = render(
      <ReasoningHarness isStreaming={false} text="final 1" />
    )

    const scroll = getScrollContainer(container)
    installScrollMetrics(scroll, { scrollHeight: 1000, clientHeight: 100 })
    scroll.scrollTop = 0

    rerender(<ReasoningHarness isStreaming={false} text="final 1 final 2" />)

    expect(scroll.scrollTop).toBe(0)
  })

  it("stops following once the user scrolls away from the bottom", () => {
    const { container, rerender } = render(
      <ReasoningHarness isStreaming text="delta 1" />
    )

    const scroll = getScrollContainer(container)
    installScrollMetrics(scroll, { scrollHeight: 1000, clientHeight: 100 })

    // Simulate the user scrolling up, away from the bottom.
    scroll.scrollTop = 0
    fireEvent.scroll(scroll)

    rerender(<ReasoningHarness isStreaming text="delta 1 delta 2 delta 3" />)

    expect(scroll.scrollTop).toBe(0)
  })
})
