import { type Metadata } from "next"
import { redirect } from "next/navigation"

import { auth } from "@/lib/auth"

export const metadata: Metadata = {
  title: "Welcome",
}
export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  // Check if a user has completed onboarding
  // If so, redirect them to /workflows
  if (auth().sessionClaims?.metadata.onboardingComplete === true) {
    return redirect("/workflows")
  }

  return <>{children}</>
}
