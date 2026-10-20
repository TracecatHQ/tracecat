import { fireEvent, render, screen } from "@testing-library/react"
import { useState } from "react"
import type { CaseFilterField, Filter } from "@/client"
import { FilterNode } from "./case-filter-node"
import { includeUnsetCondition } from "./case-filter-utils"

const fields: CaseFilterField[] = [
  {
    field: "payload.<path>",
    label: "Payload",
    kind: "json_scalar",
    operators: ["eq", "ne", "is_null"],
  },
  {
    field: "fields.active",
    label: "Active",
    kind: "boolean",
    operators: ["eq", "ne", "is_null"],
  },
  {
    field: "created_at",
    label: "Created",
    kind: "temporal",
    input_type: "datetime-local",
    operators: ["eq", "gte"],
  },
  {
    field: "fields.due",
    label: "Due",
    kind: "temporal",
    input_type: "date",
    operators: ["eq"],
  },
]
function setup(initial: Filter) {
  function Example() {
    const [node, setNode] = useState(initial)
    return (
      <>
        <FilterNode
          node={node}
          onChange={setNode}
          onRemove={() => {}}
          fields={fields}
          depth={1}
          maxDepth={4}
          canAdd
        />
        <output data-testid="draft">{JSON.stringify(node)}</output>
      </>
    )
  }
  render(<Example />)
}
function draft(): Filter {
  return JSON.parse(screen.getByTestId("draft").textContent ?? "null")
}

test("sends local date/time as an absolute instant and displays it in local time", () => {
  const initial = new Date(2026, 0, 20, 9, 30).toISOString()
  setup({ field: "created_at", op: "gte", value: initial })
  const input = screen.getByLabelText("Condition value")
  expect(input).toHaveValue("2026-01-20T09:30")
  fireEvent.change(input, { target: { value: "2026-07-20T10:15" } })
  expect(draft()).toEqual({
    field: "created_at",
    op: "gte",
    value: new Date(2026, 6, 20, 10, 15).toISOString(),
  })
  expect(input).toHaveValue("2026-07-20T10:15")
})

test("keeps date-only values unchanged", () => {
  setup({ field: "fields.due", op: "eq", value: "2026-01-20" })
  fireEvent.change(screen.getByLabelText("Condition value"), {
    target: { value: "2026-07-20" },
  })
  expect(draft()).toEqual({
    field: "fields.due",
    op: "eq",
    value: "2026-07-20",
  })
})

test.each(["fields.active", "payload.active"])(
  "restores a boolean after is_null for %s",
  (field) => {
    setup({ field, op: "eq", value: false })
    fireEvent.change(screen.getByLabelText("Condition operator"), {
      target: { value: "is_null" },
    })
    fireEvent.change(screen.getByLabelText("Condition operator"), {
      target: { value: "ne" },
    })
    expect(draft()).toEqual({ field, op: "ne", value: true })
    fireEvent.change(screen.getByLabelText("Condition value"), {
      target: { value: "false" },
    })
    expect(draft()).toEqual({ field, op: "ne", value: false })
  }
)

test("preserves numeric payload type through an unset round trip", () => {
  setup({ field: "payload.score", op: "eq", value: 5 })
  fireEvent.change(screen.getByLabelText("Condition operator"), {
    target: { value: "is_null" },
  })
  fireEvent.change(screen.getByLabelText("Condition operator"), {
    target: { value: "eq" },
  })
  expect(draft()).toEqual({ field: "payload.score", op: "eq", value: 0 })
})

test("row identity survives edits, include-unset wrapping, group toggles and sibling removal", () => {
  setup({
    and: [
      { field: "payload.score", op: "eq", value: 5 },
      { field: "payload.label", op: "eq", value: "" },
    ],
  })
  fireEvent.change(screen.getAllByLabelText("Payload path")[1], {
    target: { value: "name" },
  })
  fireEvent.click(screen.getAllByLabelText("Include unset")[1])
  fireEvent.change(screen.getByLabelText("Group matching"), {
    target: { value: "any" },
  })
  fireEvent.click(
    screen.getAllByRole("button", { name: "Remove condition" })[0]
  )
  expect(screen.getByLabelText("Payload value type")).toHaveValue("text")
  fireEvent.change(screen.getByLabelText("Condition value"), {
    target: { value: "005" },
  })
  expect(draft()).toEqual({
    or: [
      {
        or: [
          { field: "payload.name", op: "eq", value: "005" },
          { field: "payload.name", op: "is_null" },
        ],
      },
    ],
  })
})

test("recognizes include-unset in either order but not two unset leaves", () => {
  const condition = {
    field: "payload.name",
    op: "ne" as const,
    value: "synthetic",
  }
  const unset = { field: "payload.name", op: "is_null" as const }
  expect(includeUnsetCondition({ or: [unset, condition] })).toEqual(condition)
  expect(includeUnsetCondition({ or: [condition, unset] })).toEqual(condition)
  expect(includeUnsetCondition({ or: [unset, unset] })).toBeNull()
  expect(
    includeUnsetCondition({
      or: [condition, { ...unset, field: "payload.other" }],
    })
  ).toBeNull()
})
