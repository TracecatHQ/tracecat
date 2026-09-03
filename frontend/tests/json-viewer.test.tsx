/**
 * @jest-environment jsdom
 */

import { render, screen } from "@testing-library/react"
import type { ComponentProps, ComponentType, MouseEvent } from "react"
import {
  buildFlattenedJsonPath,
  buildJsonPath,
  JsonViewWithControls,
  serializeJsonPayload,
} from "@/components/json-viewer"
import { TooltipProvider } from "@/components/ui/tooltip"

jest.mock("react18-json-view", () => {
  return {
    __esModule: true,
    default: ({
      src,
      CopyComponent,
      CustomOperation,
      customizeCopy,
    }: {
      src: unknown
      CopyComponent: ComponentType<{
        onClick: (event: MouseEvent<HTMLButtonElement>) => void
        className: string
      }>
      CustomOperation?: ComponentType<{ node: unknown }>
      customizeCopy: (
        node: unknown,
        nodeMeta?: { currentPath: string[] }
      ) => string
    }) => {
      const jsonPathNode =
        typeof src === "object" &&
        src !== null &&
        !Array.isArray(src) &&
        "foo" in src
          ? (src as { foo: unknown }).foo
          : src
      const currentPath =
        typeof src === "object" &&
        src !== null &&
        !Array.isArray(src) &&
        "foo" in src
          ? ["foo"]
          : []

      return (
        <div data-testid="mock-json-view">
          <CopyComponent
            className="json-view--copy"
            onClick={(event: MouseEvent<HTMLButtonElement>) => {
              event.stopPropagation()
              void navigator.clipboard.writeText(
                customizeCopy(jsonPathNode, { currentPath })
              )
            }}
          />
          {CustomOperation ? <CustomOperation node={src} /> : null}
        </div>
      )
    },
  }
})

describe("JsonViewWithControls", () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  function renderViewer(props: ComponentProps<typeof JsonViewWithControls>) {
    return render(
      <TooltipProvider>
        <JsonViewWithControls {...props} />
      </TooltipProvider>
    )
  }

  it("renders only the JSONPath action by default", () => {
    renderViewer({
      src: { foo: "bar" },
      defaultExpanded: true,
    })

    expect(screen.getAllByLabelText("Copy JSONPath")).not.toHaveLength(0)
    expect(screen.queryByLabelText("Copy JSON payload")).not.toBeInTheDocument()
  })

  it("renders both copy actions in dual mode", () => {
    renderViewer({
      src: { foo: "bar" },
      defaultExpanded: true,
      copyMode: "jsonpath-and-payload",
    })

    expect(screen.getAllByLabelText("Copy JSONPath")).not.toHaveLength(0)
    expect(screen.getAllByLabelText("Copy JSON payload")).not.toHaveLength(0)
  })

  it("serializes object payloads as valid JSON", () => {
    expect(serializeJsonPayload({ foo: "bar" })).toBe('{\n  "foo": "bar"\n}')
  })

  it("serializes string payloads as valid JSON", () => {
    expect(serializeJsonPayload("bar")).toBe('"bar"')
  })

  it("serializes number payloads as valid JSON", () => {
    expect(serializeJsonPayload(42)).toBe("42")
  })

  it("builds JSONPath values with the existing prefix behavior", () => {
    expect(buildJsonPath(["foo"], "ACTIONS.reshape.result")).toBe(
      "ACTIONS.reshape.result.foo"
    )
  })

  it("quotes special-character path segments", () => {
    expect(buildJsonPath(["this.is.one.field"], "ACTIONS.reshape.result")).toBe(
      'ACTIONS.reshape.result."this.is.one.field"'
    )
  })

  it("preserves already-escaped flattened paths", () => {
    expect(
      buildFlattenedJsonPath(
        ['foo."this.is.one.field"[0]'],
        "ACTIONS.reshape.result"
      )
    ).toBe('ACTIONS.reshape.result.foo."this.is.one.field"[0]')
  })
})
