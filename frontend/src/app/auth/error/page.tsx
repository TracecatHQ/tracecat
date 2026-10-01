"use client"

import { AlertCircle } from "lucide-react"
import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { Suspense } from "react"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  AUTH_ERROR_CODES,
  type AuthErrorCode,
  parseAuthErrorCode,
} from "@/lib/auth-error"

type AuthErrorCopy = {
  title: string
  description: string
}

const DEFAULT_ERROR_COPY: AuthErrorCopy = {
  title: "Unable to login",
  description:
    "Unable to login, please try again and/or contact an administrator.",
}

function getAuthErrorCopy(code: AuthErrorCode | null): AuthErrorCopy {
  switch (code) {
    case AUTH_ERROR_CODES.SAML_ENFORCED:
      return {
        title: "Single sign-on required",
        description:
          "Your organization requires single sign-on. Go back to sign in and enter your work email to continue with SSO.",
      }
    default:
      return DEFAULT_ERROR_COPY
  }
}

function AuthErrorContent() {
  const searchParams = useSearchParams()
  const code = parseAuthErrorCode(searchParams?.get("code"))
  const { title, description } = getAuthErrorCopy(code)

  return (
    <div className="container mx-auto flex min-h-screen items-center justify-center p-6">
      <Card className="w-full max-w-lg">
        <CardHeader>
          <div className="flex items-center gap-2">
            <CardTitle>Authentication error</CardTitle>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <Alert variant="destructive">
            <AlertCircle className="size-4" />
            <AlertDescription className="space-y-2">
              <p className="font-medium">{title}</p>
              <p className="text-sm">{description}</p>
            </AlertDescription>
          </Alert>

          <Button asChild className="w-full">
            <Link href="/sign-in">Back to sign in</Link>
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}

export default function Page() {
  return (
    <Suspense fallback={null}>
      <AuthErrorContent />
    </Suspense>
  )
}
