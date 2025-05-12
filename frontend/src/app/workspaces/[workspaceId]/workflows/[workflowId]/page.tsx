import { type Metadata } from "next"
import { cookies } from "next/headers"

import { Builder } from "@/components/builder/builder"

export const metadata: Metadata = {
  title: "Builder | Tracecat",
}

export default async function BuilderPage() {
  const layout = (await cookies()).get("react-resizable-panels:layout")
  const defaultLayout = layout ? JSON.parse(layout.value) : undefined
  return <Builder defaultLayout={defaultLayout} />
}
