import { type Metadata } from "next"
import { cookies } from "next/headers"

import { Workbench } from "@/components/workbench/workbench"

export const metadata: Metadata = {
  title: "Workbench | Tracecat",
}

export default function WorkbenchPage() {
  const layout = cookies().get("react-resizable-panels:layout")
  const defaultLayout = layout ? JSON.parse(layout.value) : undefined

  return <Workbench defaultLayout={defaultLayout} />
}
