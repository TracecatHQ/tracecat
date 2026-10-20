import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useState } from "react"
import {
  type CaseFilterMetadata,
  type CaseQuery,
  casesListCaseFilterFields,
} from "@/client"
import { CancelablePromise } from "@/client/core/CancelablePromise"
import { $CasePriority, $CaseSeverity } from "@/client/schemas.gen"
import { CaseFilterEditor } from "@/components/cases/case-filter-editor"
import { QueryClient, QueryClientProvider } from "@/lib/query"
import { WorkspaceIdProvider } from "@/providers/workspace-id"

jest.mock("@/client", () => ({ casesListCaseFilterFields: jest.fn() }))
const metadata: CaseFilterMetadata = {
  max_depth: 4,
  max_conditions: 50,
  max_values: 1000,
  fields: [
    {
      field: "priority",
      label: "Priority",
      kind: "enum",
      operators: [
        "eq",
        "ne",
        "in",
        "not_in",
        "gt",
        "gte",
        "lt",
        "lte",
        "is_null",
      ],
      choices: [...$CasePriority.enum],
      range_choices: ["low", "medium", "high", "critical"],
    },
    {
      field: "severity",
      label: "Severity",
      kind: "enum",
      operators: [
        "eq",
        "ne",
        "in",
        "not_in",
        "gt",
        "gte",
        "lt",
        "lte",
        "is_null",
      ],
      choices: [...$CaseSeverity.enum],
      range_choices: [
        "informational",
        "low",
        "medium",
        "high",
        "critical",
        "fatal",
      ],
    },
    {
      field: "summary",
      label: "Summary",
      kind: "text",
      operators: ["eq", "in", "not_in"],
    },
    {
      field: "status",
      label: "Status",
      kind: "enum",
      operators: ["eq", "ne", "is_null"],
      choices: ["new", "closed"],
    },
    {
      field: "fields.risk_score",
      label: "Risk score",
      kind: "number",
      input_type: "number",
      operators: ["eq", "gte", "is_null"],
    },
    {
      field: "payload.<path>",
      label: "Payload path",
      kind: "json_scalar",
      operators: ["eq", "ne", "is_null"],
    },
  ],
}
function setup(initial: CaseQuery["filters"] = null) {
  const onApply = jest.fn()
  function Example() {
    const [value, setValue] = useState(initial)
    return (
      <CaseFilterEditor
        value={value}
        onChange={(next) => {
          setValue(next ?? null)
          onApply(next)
        }}
      />
    )
  }
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <WorkspaceIdProvider workspaceId="11111111-1111-4111-8111-111111111111">
        <Example />
      </WorkspaceIdProvider>
    </QueryClientProvider>
  )
  return onApply
}
beforeEach(() => {
  jest.clearAllMocks()
  jest
    .mocked(casesListCaseFilterFields)
    .mockImplementation(
      () => new CancelablePromise((resolve) => resolve(metadata))
    )
})

test("applies nested all/any conditions only on submit and removes an applied leaf", async () => {
  const user = userEvent.setup()
  const onApply = setup()
  expect(casesListCaseFilterFields).not.toHaveBeenCalled()
  await user.click(screen.getByRole("button", { name: "Conditions" }))
  await screen.findByLabelText("Condition field")
  await user.click(screen.getByRole("button", { name: "Add group" }))
  fireEvent.change(screen.getAllByLabelText("Group matching")[1], {
    target: { value: "any" },
  })
  fireEvent.change(screen.getAllByLabelText("Condition field")[1], {
    target: { value: "fields.risk_score" },
  })
  const numberOps = screen.getAllByLabelText("Condition operator")[1]
  expect(
    within(numberOps).queryByRole("option", { name: "contains" })
  ).not.toBeInTheDocument()
  fireEvent.change(numberOps, { target: { value: "gte" } })
  fireEvent.change(screen.getAllByLabelText("Condition value")[1], {
    target: { value: "80" },
  })
  await user.click(screen.getAllByLabelText("Include unset")[1])
  expect(onApply).not.toHaveBeenCalled()
  await user.click(screen.getByRole("button", { name: "Apply conditions" }))
  expect(onApply).toHaveBeenLastCalledWith({
    and: [
      { field: "status", op: "eq", value: "new" },
      {
        or: [
          {
            or: [
              { field: "fields.risk_score", op: "gte", value: "80" },
              { field: "fields.risk_score", op: "is_null" },
            ],
          },
        ],
      },
    ],
  })
  await user.click(
    screen.getByRole("button", { name: 'Remove Status equals "new"' })
  )
  expect(onApply.mock.calls.at(-1)?.[0].and).toHaveLength(1)
})

test("builds two payload equality conditions with independent literal paths", async () => {
  const user = userEvent.setup()
  const onApply = setup()
  await user.click(screen.getByRole("button", { name: "Conditions" }))
  await screen.findByLabelText("Condition field")
  fireEvent.change(screen.getByLabelText("Condition field"), {
    target: { value: "payload.<path>" },
  })
  fireEvent.change(screen.getByLabelText("Payload path"), {
    target: { value: "metadata.product.vendor_name" },
  })
  fireEvent.change(screen.getByLabelText("Condition value"), {
    target: { value: "Microsoft" },
  })
  await user.click(screen.getByRole("button", { name: "Add condition" }))
  fireEvent.change(screen.getAllByLabelText("Condition field")[1], {
    target: { value: "payload.<path>" },
  })
  fireEvent.change(screen.getAllByLabelText("Payload path")[1], {
    target: { value: "metadata.product.name" },
  })
  fireEvent.change(screen.getAllByLabelText("Condition value")[1], {
    target: { value: "Defender" },
  })
  await user.click(screen.getByRole("button", { name: "Apply conditions" }))
  expect(onApply).toHaveBeenLastCalledWith({
    and: [
      {
        field: "payload.metadata.product.vendor_name",
        op: "eq",
        value: "Microsoft",
      },
      { field: "payload.metadata.product.name", op: "eq", value: "Defender" },
    ],
  })
})

test("preserves unavailable saved fields until they are explicitly removed", async () => {
  const user = userEvent.setup()
  const value = {
    and: [{ field: "fields.removed", op: "eq" as const, value: "synthetic" }],
  }
  const onApply = setup(value)
  await user.click(screen.getByRole("button", { name: "Conditions (1)" }))
  await screen.findByText(
    "This field is unavailable. Edit or remove this condition."
  )
  expect(
    screen.getByRole("option", { name: "fields.removed (unavailable)" })
  ).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Apply conditions" }))
  expect(onApply).toHaveBeenLastCalledWith(value)
  await user.click(
    screen.getByRole("button", {
      name: 'Remove fields.removed equals "synthetic"',
    })
  )
  await waitFor(() => expect(onApply).toHaveBeenLastCalledWith(null))
})

test.each([
  { field: "priority", first: "low", last: "critical" },
  { field: "severity", first: "informational", last: "fatal" },
])(
  "changing $field to a range preserves every eligible threshold",
  async ({ field, first, last }) => {
    const user = userEvent.setup()
    const onApply = setup({ field, op: "eq", value: "unknown" })
    await user.click(screen.getByRole("button", { name: "Conditions (1)" }))
    await screen.findByLabelText("Condition field")
    fireEvent.change(screen.getByLabelText("Condition operator"), {
      target: { value: "gte" },
    })
    const input = screen.getByLabelText("Condition value")
    expect(input).toHaveValue(first)
    const expected = metadata.fields.find((item) => item.field === field)!
    expect(
      within(input)
        .getAllByRole("option")
        .map((option) => (option as HTMLOptionElement).value)
    ).toEqual(expected.range_choices)
    for (const value of expected.range_choices ?? []) {
      fireEvent.change(input, { target: { value } })
      expect(input).toHaveValue(value)
    }
    expect(
      within(input).queryByRole("option", { name: "unknown" })
    ).not.toBeInTheDocument()
    expect(
      within(input).queryByRole("option", { name: "other" })
    ).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText("Condition operator"), {
      target: { value: "eq" },
    })
    expect(
      within(input).getByRole("option", { name: "unknown" })
    ).toBeInTheDocument()
    expect(
      within(input).getByRole("option", { name: "other" })
    ).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText("Condition operator"), {
      target: { value: "lt" },
    })
    await user.click(screen.getByRole("button", { name: "Apply conditions" }))
    expect(onApply).toHaveBeenLastCalledWith({ field, op: "lt", value: last })
  }
)

test("preserves a saved unranked range value but blocks Apply until corrected", async () => {
  const user = userEvent.setup()
  const onApply = setup({ field: "priority", op: "gte", value: "unknown" })
  await user.click(screen.getByRole("button", { name: "Conditions (1)" }))
  await screen.findByRole("option", { name: "unknown (unavailable)" })
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Choose an available comparison value"
  )
  const apply = screen.getByRole("button", { name: "Apply conditions" })
  expect(apply).toBeDisabled()
  fireEvent.submit(apply.closest("form")!)
  expect(onApply).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText("Condition value"), {
    target: { value: "high" },
  })
  expect(apply).toBeEnabled()
  await user.click(apply)
  expect(onApply).toHaveBeenLastCalledWith({
    field: "priority",
    op: "gte",
    value: "high",
  })
})

test("rejects 1001 values, guards submission, and accepts exactly 1000 after editing", async () => {
  const user = userEvent.setup()
  const onApply = setup({
    field: "summary",
    op: "in",
    value: Array(1001).fill("synthetic"),
  })
  await user.click(screen.getByRole("button", { name: "Conditions (1)" }))
  await screen.findByLabelText("Condition value")
  expect(screen.getByRole("alert")).toHaveTextContent("at most 1000 values")
  const apply = screen.getByRole("button", { name: "Apply conditions" })
  expect(apply).toBeDisabled()
  fireEvent.submit(apply.closest("form")!)
  expect(onApply).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText("Condition value"), {
    target: { value: Array(1000).fill("synthetic").join(",") },
  })
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  expect(apply).toBeEnabled()
  await user.click(apply)
  expect(onApply).toHaveBeenLastCalledWith({
    field: "summary",
    op: "in",
    value: Array(1000).fill("synthetic"),
  })
})

test("counts values across nested lists and scalar conditions against the live limit", async () => {
  jest
    .mocked(casesListCaseFilterFields)
    .mockImplementation(
      () =>
        new CancelablePromise((resolve) =>
          resolve({ ...metadata, max_values: 3 })
        )
    )
  const user = userEvent.setup()
  const onApply = setup({
    and: [
      { field: "summary", op: "in", value: ["first", "second"] },
      {
        or: [
          { field: "summary", op: "not_in", value: ["third"] },
          { field: "status", op: "eq", value: "new" },
        ],
      },
    ],
  })
  await user.click(screen.getByRole("button", { name: "Conditions (3)" }))
  await screen.findByRole("alert")
  expect(screen.getByRole("alert")).toHaveTextContent("at most 3 values")
  const apply = screen.getByRole("button", { name: "Apply conditions" })
  expect(apply).toBeDisabled()
  fireEvent.change(screen.getAllByLabelText("Condition value")[0], {
    target: { value: "first" },
  })
  expect(apply).toBeEnabled()
  await user.click(apply)
  expect(onApply.mock.calls.at(-1)?.[0].and[0].value).toEqual(["first"])
  expect(onApply.mock.calls.at(-1)?.[0].and[1].or[0].value).toEqual(["third"])
})
