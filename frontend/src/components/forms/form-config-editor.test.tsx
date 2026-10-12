import { diagnosticCount, forEachDiagnostic } from "@codemirror/lint"
import type { EditorView } from "@codemirror/view"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useState } from "react"
import { FormConfigEditor } from "@/components/forms/form-config-editor"
import {
  InsertImageButton,
  insertImageDataUri,
} from "@/components/forms/insert-image-button"
import { toast } from "@/components/ui/use-toast"
import type { FormConfigProblem } from "@/lib/forms/yaml-diagnostics"

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

// jsdom has no layout. CodeMirror measures text through ranges, so give it
// empty measurements instead of letting each measure pass throw.
const noRects = Object.assign([], {
  item: () => null,
}) as unknown as DOMRectList
Range.prototype.getClientRects = () => noRects
Range.prototype.getBoundingClientRect = () => new DOMRect()

const WEBP_HEADER = "RIFF\x04\0\0\0WEBP"
const IMAGE = `data:image/webp;base64,${btoa(WEBP_HEADER + "x".repeat(18 * 1024 - WEBP_HEADER.length))}`
const CONFIG = `layout:
  - field: model
    widget: cards
    options:
      - value: mba
        image: "${IMAGE}"
      - value: mbp
`

function Harness({
  initial,
  problems = [],
  readOnly = false,
  onSave = () => {},
  onView,
}: {
  initial: string
  problems?: FormConfigProblem[]
  readOnly?: boolean
  onSave?: () => void
  onView?: (view: EditorView) => void
}) {
  const [value, setValue] = useState(initial)
  const [view, setView] = useState<EditorView | null>(null)
  return (
    <>
      <InsertImageButton view={view} encode={async () => IMAGE} />
      <output data-testid="value">{value}</output>
      <FormConfigEditor
        value={value}
        onChange={setValue}
        readOnly={readOnly}
        problems={problems}
        onSave={onSave}
        onCreateEditor={(created) => {
          setView(created)
          onView?.(created)
        }}
      />
    </>
  )
}

async function renderEditor(props: Parameters<typeof Harness>[0]) {
  let view: EditorView | undefined
  const result = render(
    <Harness
      {...props}
      onView={(created) => {
        view = created
      }}
    />
  )
  await waitFor(() => expect(view).toBeDefined())
  return { ...result, view: view as EditorView }
}

beforeEach(() => {
  jest.clearAllMocks()
})

describe("FormConfigEditor", () => {
  it("folds an inline image into a chip and keeps it out of the DOM", async () => {
    const { container, view } = await renderEditor({ initial: CONFIG })

    const chips = container.querySelectorAll(".cm-form-image-chip")
    expect(chips).toHaveLength(1)
    expect(chips[0]).toHaveTextContent("image 18 KB")
    expect(view.contentDOM.textContent).not.toContain("base64")
    expect(view.contentDOM.textContent).toContain("value: mbp")
    // Folding is display only: the text is untouched.
    expect(view.state.doc.toString()).toBe(CONFIG)
  })

  it("removes a chip as one character", async () => {
    const { container, view } = await renderEditor({ initial: CONFIG })
    const end = CONFIG.indexOf(IMAGE) + IMAGE.length

    act(() => view.dispatch({ selection: { anchor: end } }))
    fireEvent.keyDown(view.contentDOM, { key: "Backspace", keyCode: 8 })

    await waitFor(() =>
      expect(screen.getByTestId("value")).toHaveTextContent('image: ""')
    )
    expect(container.querySelectorAll(".cm-form-image-chip")).toHaveLength(0)
  })

  it("calls onSave on Mod-s without typing into the text", async () => {
    const onSave = jest.fn()
    const { view } = await renderEditor({ initial: "title: Laptop\n", onSave })

    fireEvent.keyDown(view.contentDOM, { key: "s", keyCode: 83, ctrlKey: true })
    fireEvent.keyDown(view.contentDOM, { key: "s", keyCode: 83, metaKey: true })

    expect(onSave).toHaveBeenCalled()
    expect(view.state.doc.toString()).toBe("title: Laptop\n")
  })

  it("marks problems in the text and clamps ones past the end", async () => {
    const text = "title: Laptop\nlayout: nope\n"
    const { view, rerender } = await renderEditor({ initial: text })

    rerender(
      <Harness
        initial={text}
        problems={[
          {
            severity: "error",
            message: "Bad layout",
            from: 14,
            to: 25,
            line: 2,
          },
          {
            severity: "warning",
            message: "Stale",
            from: 900,
            to: 950,
            line: 9,
          },
        ]}
      />
    )

    await waitFor(() => expect(diagnosticCount(view.state)).toBe(2))
    const marks: Array<[string, number, number]> = []
    forEachDiagnostic(view.state, (diagnostic, from, to) => {
      marks.push([diagnostic.message, from, to])
    })
    expect(marks).toEqual([
      ["Bad layout", 14, 25],
      ["Stale", text.length, text.length],
    ])

    rerender(<Harness initial={text} problems={[]} />)
    await waitFor(() => expect(diagnosticCount(view.state)).toBe(0))
  })

  it("does not accept typing when read-only", async () => {
    const { view } = await renderEditor({
      initial: "title: Laptop\n",
      readOnly: true,
    })

    expect(view.state.readOnly).toBe(true)
  })
})

describe("InsertImageButton", () => {
  const picture = () =>
    new File(["pixels"], "laptop.jpg", { type: "image/jpeg" })

  it("inserts the resized image at the cursor, quoted", async () => {
    const text = "options:\n  - value: mba\n    image: \n"
    const { view, container } = await renderEditor({ initial: text })
    const user = userEvent.setup()
    act(() => view.dispatch({ selection: { anchor: text.length - 1 } }))

    await user.upload(screen.getByLabelText("Image file"), picture())

    await waitFor(() =>
      expect(view.state.doc.toString()).toBe(
        `options:\n  - value: mba\n    image: "${IMAGE}"\n`
      )
    )
    expect(container.querySelectorAll(".cm-form-image-chip")).toHaveLength(1)
    expect(toast).not.toHaveBeenCalled()
  })

  it("does not double the quotes the author already typed", async () => {
    const text = 'image: ""\n'
    const { view } = await renderEditor({ initial: text })
    act(() => view.dispatch({ selection: { anchor: text.indexOf('"') + 1 } }))

    act(() => insertImageDataUri(view, IMAGE))

    expect(view.state.doc.toString()).toBe(`image: "${IMAGE}"\n`)
  })

  it("rejects a file type the server does not take, with the reason", async () => {
    const { view } = await renderEditor({ initial: "image: \n" })
    const input = screen.getByLabelText("Image file")

    // `user.upload` honours `accept`; a drop or a renamed file does not.
    fireEvent.change(input, {
      target: {
        files: [new File(["GIF89a"], "laptop.gif", { type: "image/gif" })],
      },
    })

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "Couldn't insert image",
          description: "Use a PNG, JPEG or WebP image.",
          variant: "destructive",
        })
      )
    )
    expect(view.state.doc.toString()).toBe("image: \n")
  })

  it("offers only the image types the server takes", async () => {
    await renderEditor({ initial: "" })

    expect(screen.getByLabelText("Image file")).toHaveAttribute(
      "accept",
      "image/png,image/jpeg,image/webp"
    )
    expect(screen.getByRole("button", { name: "Insert image" })).toBeEnabled()
  })
})
