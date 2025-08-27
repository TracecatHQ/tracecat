import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Entities",
}

export default function EntitiesLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
