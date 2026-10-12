import type { FormFieldBlock } from "@/client"

/**
 * Uploads one file of a `file` field and resolves to its file ID.
 *
 * This is the only seam between the `file` widget and the network. The
 * backend has no upload route yet, so nothing passes one today and picked
 * files stay local.
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

/** Ids of the help and error text a control is described by. */
export function describedBy(id: string): string {
  return `${id}-help ${id}-error`
}
