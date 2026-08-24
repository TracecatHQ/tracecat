"use client"

// import { ReactQueryDevtools } from "@tanstack/react-query-devtools"
import { type ReactNode, useState } from "react"
import { ApiError } from "@/client"
import { handleGlobalError, showFallbackErrorToast } from "@/lib/errors"
import { QueryCache, QueryClient, QueryClientProvider } from "@/lib/query"

export const DefaultQueryClientProvider = ({
  children,
}: {
  children: ReactNode
}) => {
  const [client] = useState(
    new QueryClient({
      queryCache: new QueryCache({
        onError: (error, query) => {
          // A background refresh failure should not interrupt the user while
          // previously loaded data remains available. Data-less polling
          // queries should only notify on their first terminal failure.
          if (
            query.state.data !== undefined ||
            query.state.errorUpdateCount > 1
          ) {
            return
          }
          if (handleGlobalError(error)) {
            return
          }
          const errorMessage = query.meta?.errorMessage
          showFallbackErrorToast(
            error,
            typeof errorMessage === "string" ? errorMessage : undefined
          )
        },
      }),
      defaultOptions: {
        queries: {
          // Don't retry on 4xx client errors (they won't change on retry)
          retry: (failureCount, error) => {
            if (
              error instanceof ApiError &&
              error.status >= 400 &&
              error.status < 500
            ) {
              return false
            }
            return failureCount < 3
          },
        },
      },
    })
  )

  return (
    <QueryClientProvider client={client}>
      {children}
      {/* Only included in production
      https://tanstack.com/query/latest/docs/framework/react/devtools#install-and-import-the-devtools /> */}
      {/* <ReactQueryDevtools initialIsOpen={false} buttonPosition="top-right" /> */}
    </QueryClientProvider>
  )
}
