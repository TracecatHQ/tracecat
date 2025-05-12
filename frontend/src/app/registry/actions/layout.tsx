import { Metadata } from "next"

export const metadata: Metadata = {
  title: "Actions | Registry",
}

export default function ActionsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
