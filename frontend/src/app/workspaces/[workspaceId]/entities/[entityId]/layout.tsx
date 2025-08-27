import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Entity fields",
}

export default function EntityFieldLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
