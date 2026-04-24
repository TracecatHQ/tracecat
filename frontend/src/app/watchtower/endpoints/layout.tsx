import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Endpoints | Tracecat",
}

export default function EndpointsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
