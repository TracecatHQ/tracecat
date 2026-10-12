import {
  fromFieldKey,
  fromFieldKeyValues,
  toFieldKey,
  toFieldKeyValues,
} from "@/lib/forms/field-keys"

const NAMES = [
  "model",
  "needed_by",
  "user.email",
  "items[0]",
  'say "hi"',
  "it's",
  "a_2e_",
  "a.",
  "_",
  "__",
  "naïve name",
]

describe("toFieldKey", () => {
  it.each(NAMES)("makes %j a key with only word characters", (name) => {
    expect(toFieldKey(name)).toMatch(/^\w+$/)
  })

  it.each(NAMES)("round-trips %j", (name) => {
    expect(fromFieldKey(toFieldKey(name))).toBe(name)
  })

  it("keeps distinct names distinct", () => {
    expect(new Set(NAMES.map(toFieldKey)).size).toBe(NAMES.length)
  })

  it("leaves plain names readable", () => {
    expect(toFieldKey("model")).toBe("model")
  })
})

describe("field key values", () => {
  it("re-keys values both ways", () => {
    const values = { "user.email": "maya.okafor@example.com", model: "mba" }

    const keyed = toFieldKeyValues(values)

    expect(Object.keys(keyed)).toEqual(["user_2e_email", "model"])
    expect(fromFieldKeyValues(keyed)).toEqual(values)
  })
})
