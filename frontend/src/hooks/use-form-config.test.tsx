import { act, renderHook } from "@testing-library/react"
import type { FormPreview, FormRead } from "@/client"
import {
  scaffoldFromPreview,
  useFormConfigDraft,
} from "@/hooks/use-form-config"
import { fieldBlock } from "@/lib/forms/test-fixtures"

const SENT = "title: Sent\n"

function formRead(config: FormRead["config"], version: string): FormRead {
  return {
    id: "form-1",
    workspace_id: "workspace-1",
    config,
    config_version: version,
  } as FormRead
}

/** Edit the buffer, "send" it, and make further edits before the save lands. */
function renderDraft(editAfterSending: string | null) {
  const { result, rerender } = renderHook(
    ({ form }: { form: FormRead }) => useFormConfigDraft(form),
    { initialProps: { form: formRead({ title: "Draft" }, "v1") } }
  )
  act(() => result.current.setText(SENT))
  const submitted = result.current.text
  if (editAfterSending !== null) {
    act(() => result.current.setText(editAfterSending))
  }
  // The mutation writes the saved form to the cache before it resolves.
  const saved = formRead({ title: "Sent" }, "v2")
  rerender({ form: saved })
  act(() => result.current.markSaved(saved, submitted))
  return result
}

beforeEach(() => sessionStorage.clear())

describe("useFormConfigDraft markSaved", () => {
  it("keeps edits made while the save was in flight, unsaved", () => {
    const result = renderDraft("title: Sent and more\n")

    expect(result.current.text).toBe("title: Sent and more\n")
    expect(result.current.isDirty).toBe(true)
    expect(result.current.hasRemoteChange).toBe(false)
    // The next save goes over the config this one saved.
    expect(result.current.baseVersion).toBe("v2")
    expect(
      sessionStorage.getItem("tracecat:form-config-draft:form-1")
    ).toContain("Sent and more")
  })

  it("takes the saved config when nothing changed after sending", () => {
    const result = renderDraft(null)

    expect(result.current.text).toBe(SENT)
    expect(result.current.isDirty).toBe(false)
    expect(sessionStorage.getItem("tracecat:form-config-draft:form-1")).toBe(
      null
    )
  })
})

describe("useFormConfigDraft scaffold", () => {
  const SCAFFOLD = "layout:\n  - field: model\n"

  it("shows the scaffold for an empty config as clean text", () => {
    const { result, rerender } = renderHook(
      ({ scaffold }: { scaffold: string }) =>
        useFormConfigDraft(formRead({}, "v1"), scaffold),
      { initialProps: { scaffold: "" } }
    )
    rerender({ scaffold: SCAFFOLD })

    expect(result.current.text).toBe(SCAFFOLD)
    expect(result.current.isDirty).toBe(false)
  })

  it("does not flag a remote change when the scaffold loads under edits", () => {
    const { result, rerender } = renderHook(
      ({ scaffold }: { scaffold: string }) =>
        useFormConfigDraft(formRead({}, "v1"), scaffold),
      { initialProps: { scaffold: "" } }
    )
    act(() => result.current.setText("title: Mine\n"))
    rerender({ scaffold: SCAFFOLD })

    expect(result.current.text).toBe("title: Mine\n")
    expect(result.current.hasRemoteChange).toBe(false)
  })

  it("ignores the scaffold once a config is saved", () => {
    const { result } = renderHook(() =>
      useFormConfigDraft(formRead({ title: "Saved" }, "v1"), SCAFFOLD)
    )
    expect(result.current.text).toBe("title: Saved\n")
  })
})

describe("scaffoldFromPreview", () => {
  it("lists each rendered input as a field entry", () => {
    const scaffold = scaffoldFromPreview({
      title: "Laptop",
      blocks: [
        fieldBlock({ name: "model" }),
        { type: "divider" },
        fieldBlock({ name: "reason" }),
      ],
    } as FormPreview)
    expect(scaffold.fields).toEqual(["model", "reason"])
    expect(scaffold.text).toContain(
      "layout:\n  - field: model\n  - field: reason\n"
    )
  })

  it("is empty when the form renders no inputs", () => {
    expect(scaffoldFromPreview(null)).toEqual({ text: "", fields: [] })
  })
})
