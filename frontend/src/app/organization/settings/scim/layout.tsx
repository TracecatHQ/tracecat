import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "SCIM | Organization",
}

export default function ScimLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
