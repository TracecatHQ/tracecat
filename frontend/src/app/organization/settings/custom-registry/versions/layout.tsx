import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Versions | Custom registry",
}

export default function CustomRegistryVersionsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
