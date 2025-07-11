"use client"

import type { AxiosError } from "axios"
import Image from "next/image"
import { useRouter } from "next/navigation"
import TracecatIcon from "public/icon.png"
// Error components must be Client Components
import { useEffect } from "react"
import { ApiError } from "@/client"
import { type AlertLevel, AlertNotification } from "@/components/notifications"
import { Button } from "@/components/ui/button"

type ErrorProps = Error & { digest?: string }

export default function Error({
  error,
}: {
  error: ErrorProps | AxiosError | ApiError
}) {
  const { headline, level, message, action } = refineError(error)
  useEffect(() => {
    // Log the error to an error reporting service
    console.error("log error", error)
  }, [error])
  return (
    <main className="container flex size-full max-w-[400px] flex-col items-center justify-center space-y-4">
      <Image src={TracecatIcon} alt="Tracecat" className="mb-4 size-16" />
      <h1 className="text-2xl font-medium">{headline}</h1>
      {action}
      <AlertNotification level={level} message={message} />
    </main>
  )
}
export type CustomError = {
  headline: string
  level: AlertLevel
  message: React.ReactNode
  action: React.ReactNode
}
function refineError(error: ErrorProps): CustomError {
  if (error instanceof ApiError) {
    return apiErrorHandler(error)
  } else {
    return unexpectedError(error)
  }
}

function GoHome() {
  const router = useRouter()
  return (
    <Button variant="outline" onClick={() => router.replace("/workspaces")}>
      Return to the home page
    </Button>
  )
}

function unexpectedError(error: ErrorProps | AxiosError): CustomError {
  console.log("HANDLING ERROR", error)
  return {
    headline: "Oh no! An error occurred :(",
    level: "error",
    message: error.message,
    action: <GoHome />,
  }
}

function getErrorLevel(status: number): AlertLevel {
  if (Math.floor(status / 100) === 4) {
    return "error"
  }
  return "warning"
}
function apiErrorHandler(error: ApiError): CustomError {
  const level = getErrorLevel(error.status)
  switch (error.status) {
    case 401:
      return {
        headline: "Your session has expired",
        level,
        message: "Please log in again.",
        action: <GoHome />,
      }
    case 403:
      return {
        headline: "Permission denied",
        level,
        message: JSON.stringify(error.body),
        action: <GoHome />,
      }
    case 404:
      return {
        headline: "Resource not found",
        level,
        message: "The resource you are looking for does not exist.",
        action: <GoHome />,
      }
    default:
      return {
        headline: "Oh no! An unexpected error occurred :(",
        level,
        message: (
          <div className="space-y-4">
            <b>{error.message}</b>
            <p>{JSON.stringify(error.body, null, 2)}</p>
          </div>
        ),
        action: <GoHome />,
      }
  }
}
