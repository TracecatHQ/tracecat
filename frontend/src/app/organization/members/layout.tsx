import { Metadata } from "next"

export const metadata: Metadata = {
  title: "Members | Organization",
}

export default function MembersLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
