import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { FilterMultiSelect } from "@/components/filters/filter-multi-select"

function renderWithOptions(count: number) {
  const options = Array.from({ length: count }, (_, index) => ({
    value: `option-${index}`,
    label: `Option ${index}`,
  }))
  render(
    <FilterMultiSelect
      placeholder="Tags"
      value={[]}
      options={options}
      onChange={jest.fn()}
      mode="include"
      onModeChange={jest.fn()}
    />
  )
}

describe("FilterMultiSelect", () => {
  it("hides search for short option lists", async () => {
    renderWithOptions(15)
    await userEvent.click(screen.getByRole("button", { name: "Tags" }))
    expect(await screen.findByText("Option 14")).toBeInTheDocument()
    expect(screen.queryByPlaceholderText("Search tags...")).toBeNull()
  })

  it("shows search for long option lists", async () => {
    renderWithOptions(16)
    await userEvent.click(screen.getByRole("button", { name: "Tags" }))
    expect(
      await screen.findByPlaceholderText("Search tags...")
    ).toBeInTheDocument()
  })
})
