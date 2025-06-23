"use client"

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ReactQueryDevtools } from "@tanstack/react-query-devtools"
import { type ReactNode, useState } from "react"

export const DefaultQueryClientProvider = ({
  children,
}: {
  children: ReactNode
}) => {
  const [client] = useState(new QueryClient())

  return (
    <QueryClientProvider client={client}>
      {children}
      {/* Only included in production
      https://tanstack.com/query/latest/docs/framework/react/devtools#install-and-import-the-devtools /> */}
      <ReactQueryDevtools initialIsOpen={false} />
    </QueryClientProvider>
  )
}
