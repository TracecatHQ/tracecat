import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { createRef } from "react"
import { useForm } from "react-hook-form"
import {
  AgentModelCombobox,
  type EnabledModelOption,
} from "@/components/agents/agent-model-combobox"

import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
} from "@/components/ui/form"

const options: EnabledModelOption[] = [
  {
    catalogId: "catalog-one",
    sourceId: null,
    modelName: "model-one",
    modelProvider: "openai",
    iconId: "openai",
    displayName: "Model one",
    label: "Model one",
    metadata: "openai",
    sourceName: "OpenAI",
    sourceType: "platform",
  },
  {
    catalogId: "catalog-two",
    sourceId: "source-two",
    modelName: "model-two",
    modelProvider: "anthropic",
    iconId: "anthropic",
    displayName: "Model two",
    label: "Model two",
    metadata: "anthropic",
    sourceName: "Synthetic source",
    sourceType: "custom",
    baseUrl: "https://example.com/v1",
  },
]

it("matches saved models by catalog id before legacy fields", () => {
  render(
    <AgentModelCombobox
      options={options}
      value={{
        catalogId: "catalog-two",
        modelName: "model-one",
        modelProvider: "openai",
      }}
      onChange={jest.fn()}
    />
  )
  expect(screen.getByRole("combobox")).toHaveTextContent(
    "Model twoSynthetic source"
  )
})

it("shows an unavailable saved value", () => {
  render(
    <AgentModelCombobox
      options={options}
      value={{ modelName: "removed", modelProvider: "openai" }}
      onChange={jest.fn()}
      unavailableLabel="Unavailable in this workspace"
    />
  )
  expect(screen.getByRole("combobox")).toHaveTextContent(
    "openai / removedUnavailable in this workspace"
  )
})

it("searches and emits the selected model option", async () => {
  const user = userEvent.setup()
  const onChange = jest.fn()
  render(
    <AgentModelCombobox options={options} value={{}} onChange={onChange} />
  )
  await user.click(screen.getByRole("combobox"))
  await user.type(screen.getByPlaceholderText("Search models..."), "Synthetic")
  await user.click(screen.getByRole("option", { name: /Model two/ }))
  expect(onChange).toHaveBeenCalledWith(options[1])
})

it("forwards form control props and the ref to the trigger", () => {
  const ref = createRef<HTMLButtonElement>()
  function LabelledCombobox() {
    const form = useForm({ defaultValues: { model_name: "" } })
    return (
      <Form {...form}>
        <FormField
          control={form.control}
          name="model_name"
          render={() => (
            <FormItem>
              <FormLabel>Model</FormLabel>
              <FormControl>
                <AgentModelCombobox
                  ref={ref}
                  options={options}
                  value={{}}
                  onChange={jest.fn()}
                />
              </FormControl>
            </FormItem>
          )}
        />
      </Form>
    )
  }
  render(<LabelledCombobox />)
  const trigger = screen.getByRole("combobox")
  expect(screen.getByLabelText("Model")).toBe(trigger)
  expect(trigger).toHaveAttribute("aria-describedby")
  expect(trigger).toHaveAttribute("aria-invalid", "false")
  expect(ref.current).toBe(trigger)
})

it("shows the placeholder, not the unavailable state, until options load", () => {
  const value = { modelName: "model-one", modelProvider: "openai" }
  const { rerender } = render(
    <AgentModelCombobox
      options={[]}
      value={value}
      onChange={jest.fn()}
      loaded={false}
      placeholder="No enabled models"
    />
  )
  expect(screen.getByRole("combobox")).toHaveTextContent("No enabled models")
  expect(screen.queryByText("Legacy")).not.toBeInTheDocument()
  rerender(
    <AgentModelCombobox
      options={[]}
      value={value}
      onChange={jest.fn()}
      loaded
      placeholder="No enabled models"
    />
  )
  expect(screen.getByRole("combobox")).toHaveTextContent(
    "openai / model-oneLegacy"
  )
})

it("titles the trigger and option names so truncated text stays readable", async () => {
  const user = userEvent.setup()
  render(
    <AgentModelCombobox
      options={options}
      value={{ catalogId: "catalog-one" }}
      onChange={jest.fn()}
    />
  )
  expect(screen.getByText("Model one")).toHaveAttribute("title", "Model one")
  await user.click(screen.getByRole("combobox"))
  expect(
    screen.getByRole("option", { name: /Model two/ }).querySelector("[title]")
  ).toHaveAttribute("title", "Model two")
})

it("matches a custom-source model by name and provider only when asked", () => {
  const value = { modelName: "model-two", modelProvider: "anthropic" }
  const { rerender } = render(
    <AgentModelCombobox options={options} value={value} onChange={jest.fn()} />
  )
  expect(screen.getByRole("combobox")).toHaveTextContent(
    "anthropic / model-twoLegacy"
  )
  rerender(
    <AgentModelCombobox
      options={options}
      value={value}
      onChange={jest.fn()}
      matchAnySource
    />
  )
  expect(screen.getByRole("combobox")).toHaveTextContent(
    "Model twoSynthetic source"
  )
})

it("highlights the selected model when the list is reopened", async () => {
  const user = userEvent.setup()
  render(
    <AgentModelCombobox
      options={options}
      value={{ catalogId: "catalog-two" }}
      onChange={jest.fn()}
    />
  )
  await user.click(screen.getByRole("combobox"))
  expect(screen.getByRole("option", { name: /Model two/ })).toHaveAttribute(
    "data-selected",
    "true"
  )
  expect(screen.getByRole("option", { name: /Model one/ })).toHaveAttribute(
    "data-selected",
    "false"
  )
})
