import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  CaseAddFilterMenu,
  type CaseFilterProperty,
  CaseMultiFilterChip,
} from "@/components/cases/case-filter-chips"

const TAG_OPTIONS = [
  { value: "phishing", label: "phishing" },
  { value: "malware", label: "malware" },
  { value: "vip", label: "vip" },
]

function tagProperty(
  value: string[],
  onChange: (value: string[]) => void
): CaseFilterProperty {
  return {
    kind: "multi",
    key: "tags",
    label: "Tags",
    icon: null,
    options: TAG_OPTIONS,
    value,
    onChange,
  }
}

describe("CaseMultiFilterChip", () => {
  it("reads as property, operator, and value, and removes itself", async () => {
    const onChange = jest.fn()
    render(
      <CaseMultiFilterChip
        label="Tags"
        noun="tags"
        icon={null}
        options={TAG_OPTIONS}
        value={["phishing", "malware"]}
        onChange={onChange}
        mode="include"
        onModeChange={jest.fn()}
        allowExclude={false}
      />
    )

    const chip = screen.getByRole("group", { name: "Tags filter" })
    expect(chip).toHaveTextContent("Tags")
    expect(chip).toHaveTextContent("is any of")
    expect(chip).toHaveTextContent("2 tags")

    await userEvent.click(
      screen.getByRole("button", { name: "Remove tags filter" })
    )
    expect(onChange).toHaveBeenCalledWith([])
  })

  it("names the single value and switches to exclude", async () => {
    const onModeChange = jest.fn()
    render(
      <CaseMultiFilterChip
        label="Status"
        noun="statuses"
        icon={null}
        options={[{ value: "new", label: "New" }]}
        value={["new"]}
        onChange={jest.fn()}
        mode="include"
        onModeChange={onModeChange}
      />
    )

    expect(
      screen.getByRole("group", { name: "Status filter" })
    ).toHaveTextContent(/^Status\s*is\s*New$/)
    await userEvent.click(
      screen.getByRole("button", { name: "Status operator: is" })
    )
    await userEvent.click(
      await screen.findByRole("menuitem", { name: "is not" })
    )
    expect(onModeChange).toHaveBeenCalledWith("exclude")
  })
})

describe("CaseAddFilterMenu", () => {
  it("adds a value after choosing a property", async () => {
    const onChange = jest.fn()
    render(
      <CaseAddFilterMenu
        properties={[tagProperty(["vip"], onChange)]}
        showLabel
      />
    )

    await userEvent.click(screen.getByRole("button", { name: "Add filter" }))
    await userEvent.click(await screen.findByRole("option", { name: "Tags" }))
    await userEvent.click(
      await screen.findByRole("option", { name: "malware" })
    )
    expect(onChange).toHaveBeenCalledWith(["vip", "malware"])
  })

  it("matches values directly when typing in the first step", async () => {
    const onChange = jest.fn()
    render(
      <CaseAddFilterMenu properties={[tagProperty([], onChange)]} showLabel />
    )

    await userEvent.click(screen.getByRole("button", { name: "Add filter" }))
    await userEvent.type(
      await screen.findByPlaceholderText("Filter..."),
      "phish"
    )
    await userEvent.click(
      await screen.findByRole("option", { name: "phishing" })
    )
    expect(onChange).toHaveBeenCalledWith(["phishing"])
  })
})
