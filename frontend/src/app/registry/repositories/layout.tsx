import { Metadata } from "next"

export const metadata: Metadata = {
  title: "Repositories | Registry",
}

export default function ActionsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
