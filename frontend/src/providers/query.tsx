"use client"

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
// import { ReactQueryDevtools } from "@tanstack/react-query-devtools"
import { type ReactNode, useState } from "react"
import { ApiError } from "@/client"

export const DefaultQueryClientProvider = ({
  children,
}: {
  children: ReactNode
}) => {
  const [client] = useState(
    new QueryClient({
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
