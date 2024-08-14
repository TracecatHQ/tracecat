"use client"

import { ApiError } from "@/client"

import ErrorPage from "@/components/error"

export default function Error({
  error,
}: {
  error: ApiError | (Error & { digest?: string })
}) {
  return <ErrorPage error={error} />
}
