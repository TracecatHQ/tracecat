import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Repository | Custom registry",
}

export default function CustomRegistryLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
