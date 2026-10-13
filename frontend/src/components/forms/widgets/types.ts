import type { FormFieldBlock } from "@/client"

/**
 * Uploads one file of a `file` field and resolves to its file ID.
 *
 * This is the only seam between the `file` widget and the network. The live
 * requester page passes `uploadFormFile`; a preview passes nothing, so picked
 * files stay local. A rejection's message is shown on the file.
 */
export type FormFileUploader = (args: {
  workspaceId: string
  formId: string
  field: string
  file: File
}) => Promise<{ id: string }>

/** What a widget knows about the form it is rendered in. */
export interface FormWidgetRuntime {
  formId: string
  workspaceId: string
  /** `preview` renders the form in the builder and never submits. */
  mode: "live" | "preview"
  uploadFile?: FormFileUploader
}

/** Props every form widget takes. */
export interface FormWidgetProps {
  /** DOM id of the control. The label uses `${id}-label`. */
  id: string
  field: FormFieldBlock
  value: unknown
  onChange: (value: unknown) => void
  onBlur: () => void
  disabled: boolean
  invalid: boolean
  runtime: FormWidgetRuntime
}

/**
 * Ids of the help and error text a control is described by.
 *
 * Names only the elements the field row renders: help text when the field has
 * some, the error while the field is invalid. `undefined` when there is none.
 */
export function describedBy(
  id: string,
  field: FormFieldBlock,
  invalid: boolean
): string | undefined {
  const ids: string[] = []
  if (field.help) {
    ids.push(`${id}-help`)
  }
  if (invalid) {
    ids.push(`${id}-error`)
  }
  return ids.length > 0 ? ids.join(" ") : undefined
}
