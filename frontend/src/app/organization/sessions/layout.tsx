import { Metadata } from "next"

export const metadata: Metadata = {
  title: "Sessions | Organization",
}

export default function SessionsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
