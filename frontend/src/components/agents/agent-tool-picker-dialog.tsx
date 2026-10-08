"use client"

import { usePresetToolFields } from "@/components/agents/agent-preset-tools-list"
import {
  ToolPickerDialog,
  type ToolPickerDialogProps,
} from "@/components/tools/tool-picker-dialog"

/** Props for the preset form's tool picker; the selection comes from the form. */
export type AgentToolPickerDialogProps = Omit<
  ToolPickerDialogProps,
  "value" | "onChange"
>

/** React Hook Form adapter for the preset form's tool picker. */
export function AgentToolPickerDialog(props: AgentToolPickerDialogProps) {
  const { value, onChange } = usePresetToolFields()
  return <ToolPickerDialog {...props} value={value} onChange={onChange} />
}
