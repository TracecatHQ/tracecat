import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Email Settings | Tracecat",
}

export default function EmailLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
