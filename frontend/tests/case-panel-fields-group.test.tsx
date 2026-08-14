/**
 * @jest-environment jsdom
 */

import { render, screen } from "@testing-library/react"
import type { CaseFieldRead } from "@/client"
import { CasePanelFieldsGroup } from "@/components/cases/case-panel-fields-group"

// The field dialogs pull in the rich-text and CodeMirror editors transitively.
jest.mock("@/components/cases/case-description-editor", () => ({
  CaseDescriptionEditor: () => <textarea aria-label="Rich text editor" />,
}))

jest.mock("@uiw/react-codemirror", () => ({
  __esModule: true,
  default: () => <textarea aria-label="JSON editor" />,
}))

beforeEach(() => {
  jest.clearAllMocks()
})

const ROW_CLASS = "case-field-row"
const LABEL_CLASS = "case-field-label"
const CONTROL_CLASS = "case-field-control"

function makeField(
  overrides: Pick<CaseFieldRead, "id" | "type" | "value"> &
    Partial<CaseFieldRead>
): CaseFieldRead {
  return {
    // The API defaults a field's display name to its identifier.
    display_name: overrides.id,
    description: "",
    nullable: true,
    default: null,
    reserved: false,
    options: null,
    kind: null,
    ...overrides,
  }
}

// One plain field plus one dropdown-backed field with a value, so the
// alignment assertions have something that could plausibly lead a row.
const FIELDS: CaseFieldRead[] = [
  makeField({ id: "reporter", type: "TEXT", value: "analyst@example.com" }),
  makeField({
    id: "verdict",
    type: "SELECT",
    value: "malicious",
    options: ["malicious", "benign"],
  }),
]

function renderGroup(showAll: boolean) {
  return render(
    <CasePanelFieldsGroup
      customFields={FIELDS}
      visibleCustomFields={showAll ? FIELDS : [FIELDS[0]]}
      showAll={showAll}
      onToggleShowAll={jest.fn()}
      updateCase={jest.fn().mockResolvedValue(undefined)}
      rowClassName={ROW_CLASS}
      labelClassName={LABEL_CLASS}
      controlClassName={CONTROL_CLASS}
      onRowClick={jest.fn()}
    />
  )
}

function fieldRows(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(`.${ROW_CLASS}`))
}

describe.each([
  ["hiding empty fields", false, 1],
  ["showing all fields", true, 2],
])("CasePanelFieldsGroup when %s", (_name, showAll, expectedRows) => {
  it("renders one row per visible field", () => {
    const { container } = renderGroup(showAll)

    expect(fieldRows(container)).toHaveLength(expectedRows)
    expect(
      screen.getByRole("button", {
        name: showAll ? "Hide empty fields" : "View all fields",
      })
    ).toBeInTheDocument()
  })

  it("starts every row with its label span", () => {
    const { container } = renderGroup(showAll)
    const rows = fieldRows(container)

    expect(rows).toHaveLength(expectedRows)
    for (const row of rows) {
      const first = row.firstElementChild
      expect(first).not.toBeNull()
      expect(first?.tagName).toBe("SPAN")
      expect(first).toHaveClass(LABEL_CLASS)
    }
  })

  it("renders no button ahead of the label inside a row", () => {
    // jsdom has no layout, so "identical left alignment" is asserted
    // structurally: nothing precedes the label span in any row.
    const { container } = renderGroup(showAll)
    const rows = fieldRows(container)

    expect(rows).toHaveLength(expectedRows)
    for (const row of rows) {
      const label = row.querySelector(`.${LABEL_CLASS}`)
      if (!label) throw new Error("row is missing its label span")
      for (const button of Array.from(row.querySelectorAll("button"))) {
        // DOCUMENT_POSITION_FOLLOWING === 4: the button comes after the label.
        expect(
          label.compareDocumentPosition(button) &
            Node.DOCUMENT_POSITION_FOLLOWING
        ).toBeTruthy()
      }
    }
  })

  it("renders no row-level clear button", () => {
    const { container } = renderGroup(showAll)

    for (const row of fieldRows(container)) {
      // The only clear affordance lives inside the control, never leading the
      // row, and it is scoped to a field id.
      const clearButtons = Array.from(
        row.querySelectorAll<HTMLButtonElement>("button")
      ).filter((button) =>
        /^clear/i.test(button.getAttribute("aria-label") ?? "")
      )
      for (const button of clearButtons) {
        expect(button.getAttribute("aria-label")).toMatch(/^Clear \w+ field$/)
        expect(row.firstElementChild).not.toBe(button)
        expect(row.firstElementChild?.contains(button)).toBe(false)
      }
    }
  })
})

describe("CasePanelFieldsGroup clear affordance placement", () => {
  it("renders no clear affordance while the dropdown is closed", () => {
    // Clearing lives inside the dropdown itself now, so a populated SELECT
    // row must not render any clear control at rest.
    renderGroup(true)

    expect(
      screen.queryByRole("button", { name: "Clear verdict field" })
    ).not.toBeInTheDocument()
  })
})

describe("CasePanelFieldsGroup empty state", () => {
  it("shows a placeholder when no custom fields are configured", () => {
    render(
      <CasePanelFieldsGroup
        customFields={[]}
        visibleCustomFields={[]}
        showAll={false}
        onToggleShowAll={jest.fn()}
        updateCase={jest.fn().mockResolvedValue(undefined)}
        rowClassName={ROW_CLASS}
        labelClassName={LABEL_CLASS}
        controlClassName={CONTROL_CLASS}
        onRowClick={jest.fn()}
      />
    )

    expect(screen.getByText("No custom fields configured")).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "View all fields" })
    ).not.toBeInTheDocument()
  })
})
