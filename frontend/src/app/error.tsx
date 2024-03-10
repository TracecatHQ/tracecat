"use client"

// Error components must be Client Components
import { useEffect } from "react"

import { AlertDestructive } from "@/components/alert-destructive"

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    // Log the error to an error reporting service
    console.error(error)
  }, [error])

  return (
    <main className="container flex h-full w-full max-w-[400px] items-center justify-center">
      <AlertDestructive message={error.message} reset={reset} />
    </main>
  )
}
