import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Custom fields | Workspace",
}

export default function CustomFieldsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
