import { fireEvent, render, screen } from "@testing-library/react"
import { useState } from "react"
import type { RegistryActionReadMinimal } from "@/client"
import {
  ActionListbox,
  searchActions,
  useActionSearch,
} from "@/components/registry/action-listbox"

let originalHeight: PropertyDescriptor | undefined
let originalScrollTo: typeof HTMLElement.prototype.scrollTo
beforeAll(() => {
  originalHeight = Object.getOwnPropertyDescriptor(
    HTMLElement.prototype,
    "offsetHeight"
  )
  originalScrollTo = HTMLElement.prototype.scrollTo
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
    configurable: true,
    get: () => 300,
  })
  HTMLElement.prototype.scrollTo = function scrollTo(
    options?: ScrollToOptions | number
  ) {
    if (typeof options !== "number") this.scrollTop = options?.top ?? 0
    this.dispatchEvent(new Event("scroll"))
  }
})
afterAll(() => {
  if (originalHeight)
    Object.defineProperty(HTMLElement.prototype, "offsetHeight", originalHeight)
  HTMLElement.prototype.scrollTo = originalScrollTo
})

function makeAction(i: number): RegistryActionReadMinimal {
  const name = `action_${String(i).padStart(4, "0")}`
  return {
    id: `id-${i}`,
    name,
    namespace: "tools.synthetic",
    action: `tools.synthetic.${name}`,
    default_title: `Synthetic action ${i}`,
    description: "",
    type: "template",
    origin: "tracecat",
  }
}

const ACTIONS = Array.from({ length: 1500 }, (_, i) => makeAction(i))
const KEYS = ["action", "default_title"] as const

function Harness({ onSelect }: { onSelect: (action: string) => void }) {
  const [query, setQuery] = useState("")
  const { results } = useActionSearch(ACTIONS, query, KEYS)
  return (
    <ActionListbox
      results={results}
      query={query}
      onQueryChange={setQuery}
      onSelect={(action) => onSelect(action.action)}
      renderItem={({ action }) => <span>{action.default_title}</span>}
    />
  )
}

describe("ActionListbox", () => {
  it("virtualizes 1,500 actions with fewer than 30 mounted options", () => {
    render(<Harness onSelect={jest.fn()} />)
    const options = screen.getAllByRole("option")
    expect(options.length).toBeGreaterThan(0)
    expect(options.length).toBeLessThan(30)
  })

  it("filters, navigates with the keyboard and selects with Enter", () => {
    const onSelect = jest.fn()
    render(<Harness onSelect={onSelect} />)
    const input = screen.getByRole("combobox")
    fireEvent.change(input, { target: { value: "action_0042" } })
    expect(screen.getByText("Synthetic action 42")).toBeInTheDocument()
    fireEvent.keyDown(input, { key: "Enter" })
    expect(onSelect).toHaveBeenCalledWith("tools.synthetic.action_0042")
  })

  it("moves the active option with arrow keys", () => {
    const onSelect = jest.fn()
    render(<Harness onSelect={onSelect} />)
    const input = screen.getByRole("combobox")
    fireEvent.keyDown(input, { key: "ArrowDown" })
    fireEvent.keyDown(input, { key: "ArrowDown" })
    fireEvent.keyDown(input, { key: "ArrowUp" })
    fireEvent.keyDown(input, { key: "Enter" })
    expect(onSelect).toHaveBeenCalledWith("tools.synthetic.action_0001")
  })

  it("selects an option on click", () => {
    const onSelect = jest.fn()
    render(<Harness onSelect={onSelect} />)
    fireEvent.click(screen.getByText("Synthetic action 2"))
    expect(onSelect).toHaveBeenCalledWith("tools.synthetic.action_0002")
  })

  it("shows the empty state when nothing matches", () => {
    render(<Harness onSelect={jest.fn()} />)
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "zzzzqqq" },
    })
    expect(screen.getByText("No actions found.")).toBeInTheDocument()
  })
})

describe("searchActions", () => {
  it("keeps input order for an empty query", () => {
    const results = searchActions(ACTIONS.slice(0, 3), "  ", KEYS)
    expect(results.map((r) => r.action.name)).toEqual([
      "action_0000",
      "action_0001",
      "action_0002",
    ])
  })
})
