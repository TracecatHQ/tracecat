import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Actions | Tracecat",
}

export default function ActionsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
