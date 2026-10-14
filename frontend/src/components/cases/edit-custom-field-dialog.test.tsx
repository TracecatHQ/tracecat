import { render, screen } from "@testing-library/react"
import type { CaseFieldReadMinimal } from "@/client"
import { EditCustomFieldDialog } from "@/components/cases/edit-custom-field-dialog"

jest.mock("@/client", () => ({
  ApiError: class ApiError extends Error {},
  casesUpdateField: jest.fn(),
}))
jest.mock("@/lib/query", () => ({ useQueryClient: () => ({}) }))
jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "ws-1",
}))

function makeField(
  overrides: Partial<CaseFieldReadMinimal> = {}
): CaseFieldReadMinimal {
  return {
    id: "category",
    display_name: "Category",
    type: "MULTI_SELECT",
    description: "",
    nullable: true,
    default: null,
    reserved: false,
    options: ["alpha", "beta", "gamma"],
    kind: null,
    required_on_closure: false,
    ...overrides,
  }
}

describe("EditCustomFieldDialog", () => {
  it.each(["SELECT", "MULTI_SELECT"] as const)(
    "keeps existing options when opening a %s field",
    (type) => {
      render(
        <EditCustomFieldDialog
          open
          field={makeField({ type })}
          onOpenChange={() => {}}
        />
      )

      for (const option of ["alpha", "beta", "gamma"]) {
        expect(
          screen.getByRole("button", { name: `Remove ${option}` })
        ).toBeInTheDocument()
      }
    }
  )

  it("keeps existing options when reopened for another field", () => {
    const { rerender } = render(
      <EditCustomFieldDialog
        open
        field={makeField({ id: "first", type: "SELECT" })}
        onOpenChange={() => {}}
      />
    )
    rerender(
      <EditCustomFieldDialog
        open={false}
        field={null}
        onOpenChange={() => {}}
      />
    )
    rerender(
      <EditCustomFieldDialog
        open
        field={makeField({ id: "second", options: ["delta", "epsilon"] })}
        onOpenChange={() => {}}
      />
    )

    for (const option of ["delta", "epsilon"]) {
      expect(
        screen.getByRole("button", { name: `Remove ${option}` })
      ).toBeInTheDocument()
    }
  })

  it("keeps the existing multi-select default when opening", () => {
    render(
      <EditCustomFieldDialog
        open
        field={makeField({ default: '["beta"]' })}
        onOpenChange={() => {}}
      />
    )

    // One tag in the options input and one in the default values input.
    expect(screen.getAllByRole("button", { name: "Remove beta" })).toHaveLength(
      2
    )
  })
})
