"use client"

import { AlertCircle, ChevronLeft } from "lucide-react"
import { useSearchParams } from "next/navigation"
import { Suspense } from "react"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { env } from "@/env.mjs"

function OAuthErrorContent() {
  const searchParams = useSearchParams()

  const error = searchParams?.get("error") || "unknown_error"
  const errorDescription = searchParams?.get("error_description")

  const handleBackToIntegrations = () => {
    // Redirect to the public app URL
    window.location.href = env.NEXT_PUBLIC_APP_URL
  }

  // Clean up URL-encoded error descriptions
  const cleanDescription = errorDescription
    ? decodeURIComponent(errorDescription.replace(/\+/g, " "))
    : "An error occurred during OAuth authentication."

  return (
    <div className="container mx-auto flex min-h-screen items-center justify-center p-6">
      <Card className="w-full max-w-lg">
        <CardHeader>
          <div className="flex items-center gap-2">
            <CardTitle>OAuth connection failed</CardTitle>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <Alert variant="destructive">
            <AlertCircle className="size-4" />
            <AlertDescription className="space-y-2">
              <p className="font-medium">Error: {error}</p>
              <p className="text-sm">{cleanDescription}</p>
            </AlertDescription>
          </Alert>

          <Button onClick={handleBackToIntegrations} className="w-full">
            <ChevronLeft className="mr-2 size-4" />
            Back to home
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}

export default function OAuthErrorPage() {
  return (
    <Suspense fallback={null}>
      <OAuthErrorContent />
    </Suspense>
  )
}
