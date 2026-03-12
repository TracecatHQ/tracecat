import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Custom registry | Organization",
}

export default function CustomRegistryLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
