import { restoreCaseFilter } from "./case-filters"

const leaf = { field: "fields.removed", op: "eq", value: "deleted-option" }
test("preserves valid unavailable fields and options, including nested groups", () => {
  const tree = { and: [leaf, { not: { or: [leaf] } }] }
  expect(restoreCaseFilter(tree)).toEqual(tree)
})
test.each([
  null,
  3,
  "text",
  [],
  {},
  { and: [] },
  { or: [null] },
  { and: [leaf], or: [leaf] },
  { ...leaf, extra: true },
  { ...leaf, op: "unknown" },
  { ...leaf, field: "" },
  { ...leaf, value: [["nested"]] },
  { ...leaf, value: [null] },
  { ...leaf, value: {} },
  { ...leaf, value: Infinity },
  { not: { not: { not: { not: leaf } } } },
  { and: Array(51).fill(leaf) },
  { ...leaf, value: Array(1001).fill("x") },
])("discards malformed or over-budget persisted trees: %j", (value) => {
  expect(restoreCaseFilter(value)).toBeNull()
})
test("counts values across leaves and permits exact server limits", () => {
  expect(
    restoreCaseFilter({ ...leaf, value: Array(1000).fill("x") })
  ).not.toBeNull()
  expect(restoreCaseFilter({ and: Array(50).fill(leaf) })).not.toBeNull()
  expect(
    restoreCaseFilter({
      and: [
        { ...leaf, value: Array(600).fill("x") },
        { ...leaf, value: Array(401).fill("x") },
      ],
    })
  ).toBeNull()
})
