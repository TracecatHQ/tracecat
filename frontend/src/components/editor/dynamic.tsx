"use client"

import dynamic from "next/dynamic"

import { type CustomEditorProps } from "@/components/editor/editor"
import { CenteredSpinner } from "@/components/loading/spinner"

let DynamicCustomEditor: React.ComponentType<CustomEditorProps>
if (typeof window !== "undefined") {
  DynamicCustomEditor = dynamic(() => import("@/components/editor/editor"), {
    ssr: false,
    loading: () => <CenteredSpinner />,
  })
} else {
  const EmptyEditor = (props: CustomEditorProps) => <></>
  EmptyEditor.displayName = "EmptyEditor"
  DynamicCustomEditor = EmptyEditor
}

export { DynamicCustomEditor }
