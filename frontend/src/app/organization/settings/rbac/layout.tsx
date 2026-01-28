import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Access control | Organization",
}

export default function RbacLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
