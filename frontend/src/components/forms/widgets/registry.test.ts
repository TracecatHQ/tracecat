import { $FormWidget } from "@/client/schemas.gen"
import { FORM_WIDGETS } from "@/components/forms/widgets/registry"

jest.mock("@/client", () => ({}))

describe("FORM_WIDGETS", () => {
  // The registry's type already fails typecheck when a widget is missing.
  // This reads the generated OpenAPI schema instead, so it also catches a
  // widget the client has that the backend dropped.
  it("has exactly one component for every widget the backend defines", () => {
    expect(Object.keys(FORM_WIDGETS).sort()).toEqual(
      [...$FormWidget.enum].sort()
    )
  })

  it.each(Object.entries(FORM_WIDGETS))(
    "maps %s to a component",
    (_widget, component) => {
      expect(typeof component).toBe("function")
    }
  )
})
