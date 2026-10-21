import { act, renderHook } from "@testing-library/react"
import type { FormRead } from "@/client"
import { useFormConfigDraft } from "@/hooks/use-form-config"

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
