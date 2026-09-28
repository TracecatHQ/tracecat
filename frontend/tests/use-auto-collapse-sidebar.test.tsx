/**
 * @jest-environment jsdom
 */

import { fireEvent, render, screen } from "@testing-library/react"
import { useCallback, useState } from "react"
import { useSidebar } from "@/components/ui/sidebar"
import { useAutoCollapseSidebar } from "@/hooks/use-auto-collapse-sidebar"
import { useMediaQuery } from "@/hooks/use-media-query"

jest.mock("@/components/ui/sidebar", () => ({
  useSidebar: jest.fn(),
}))

jest.mock("@/hooks/use-media-query", () => ({
  useMediaQuery: jest.fn(),
}))

const mockUseSidebar = useSidebar as jest.Mock
const mockUseMediaQuery = useMediaQuery as jest.Mock

interface SetOpenCall {
  open: boolean
  persist?: boolean
}

let setOpenCalls: SetOpenCall[] = []

beforeEach(() => {
  setOpenCalls = []
  jest.clearAllMocks()
})

/**
 * Drives the hook off real React state so `open` reflects each `setOpen`, and
 * so `setOpen`'s identity churns with `open` exactly as the real provider's
 * does — that churn is what the hook's crossing guard has to absorb.
 */
function Harness({
  narrow,
  initialOpen = true,
}: {
  narrow: boolean
  initialOpen?: boolean
}) {
  const [open, setOpenState] = useState(initialOpen)
  const setOpen = useCallback(
    (
      next: boolean | ((prev: boolean) => boolean),
      options?: { persist?: boolean }
    ) => {
      const resolved = typeof next === "function" ? next(open) : next
      setOpenCalls.push({ open: resolved, persist: options?.persist })
      setOpenState(resolved)
    },
    [open]
  )

  mockUseSidebar.mockReturnValue({ open, setOpen })
  mockUseMediaQuery.mockReturnValue(narrow)
  useAutoCollapseSidebar()

  return (
    <>
      <span data-testid="open">{String(open)}</span>
      {/* Stands in for the sidebar trigger: a user toggle, so it persists. */}
      <button type="button" onClick={() => setOpen(!open, { persist: true })}>
        toggle
      </button>
    </>
  )
}

function openState() {
  return screen.getByTestId("open").textContent
}

function toggle() {
  fireEvent.click(screen.getByRole("button", { name: "toggle" }))
}

describe("useAutoCollapseSidebar", () => {
  it("collapses on narrowing without persisting", () => {
    const { rerender } = render(<Harness narrow={false} />)
    expect(openState()).toBe("true")

    rerender(<Harness narrow={true} />)

    expect(openState()).toBe("false")
    expect(setOpenCalls).toEqual([{ open: false, persist: false }])
  })

  it("restores its own collapse on widening", () => {
    const { rerender } = render(<Harness narrow={false} />)
    rerender(<Harness narrow={true} />)
    rerender(<Harness narrow={false} />)

    expect(openState()).toBe("true")
    expect(setOpenCalls).toEqual([
      { open: false, persist: false },
      { open: true, persist: false },
    ])
  })

  it("leaves a nav the user collapsed while wide alone", () => {
    const { rerender } = render(<Harness narrow={false} initialOpen={false} />)
    rerender(<Harness narrow={true} />)
    rerender(<Harness narrow={false} />)

    expect(openState()).toBe("false")
    expect(setOpenCalls).toEqual([])
  })

  it("does not undo a manual re-open while still narrow", () => {
    const { rerender } = render(<Harness narrow={false} />)
    rerender(<Harness narrow={true} />)

    toggle()

    expect(openState()).toBe("true")
  })

  // The auto-collapse flag used to survive every later manual toggle, so a nav
  // the user reopened and then closed by hand reappeared on widening — against
  // a persisted preference that said collapsed.
  it("does not restore a nav the user collapsed after reopening it", () => {
    const { rerender } = render(<Harness narrow={false} />)
    rerender(<Harness narrow={true} />)

    toggle()
    toggle()
    expect(openState()).toBe("false")

    rerender(<Harness narrow={false} />)

    expect(openState()).toBe("false")
    expect(setOpenCalls).toEqual([
      { open: false, persist: false },
      { open: true, persist: true },
      { open: false, persist: true },
    ])
  })

  it("collapses when it mounts already narrow", () => {
    render(<Harness narrow={true} />)

    expect(openState()).toBe("false")
    expect(setOpenCalls).toEqual([{ open: false, persist: false }])
  })
})
