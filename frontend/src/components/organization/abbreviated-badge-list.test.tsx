import { render, screen } from "@testing-library/react"
import { AbbreviatedBadgeList } from "@/components/organization/abbreviated-badge-list"
import { TooltipProvider } from "@/components/ui/tooltip"
import { abbreviateName } from "@/lib/rbac"

const items = [
  { id: "1", name: "Engineering" },
  { id: "2", name: "Security" },
  { id: "3", name: "Finance" },
  { id: "4", name: "Legal" },
  { id: "5", name: "Support" },
]

function renderList(listItems: typeof items, max?: number) {
  return render(
    <TooltipProvider>
      <AbbreviatedBadgeList
        items={listItems}
        abbreviate={abbreviateName}
        max={max}
      />
    </TooltipProvider>
  )
}

describe("AbbreviatedBadgeList", () => {
  it("renders an abbreviation per item", () => {
    renderList(items.slice(0, 3))

    expect(screen.getByText("ENG")).toBeInTheDocument()
    expect(screen.getByText("SEC")).toBeInTheDocument()
    expect(screen.getByText("FIN")).toBeInTheDocument()
    expect(screen.queryByText(/^\+/)).not.toBeInTheDocument()
  })

  it("collapses items beyond max into a +N badge", () => {
    renderList(items, 2)

    expect(screen.getByText("ENG")).toBeInTheDocument()
    expect(screen.getByText("SEC")).toBeInTheDocument()
    expect(screen.queryByText("FIN")).not.toBeInTheDocument()
    expect(screen.getByText("+3")).toBeInTheDocument()
  })

  it("renders a dash when empty", () => {
    renderList([])

    expect(screen.getByText("-")).toBeInTheDocument()
  })
})
