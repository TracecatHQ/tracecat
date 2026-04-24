import type { Metadata } from "next"
import { redirect } from "next/navigation"

export const metadata: Metadata = {
  title: "Watchtower | Tracecat",
}

export default function WatchtowerPage() {
  return redirect("/watchtower/endpoints")
}
