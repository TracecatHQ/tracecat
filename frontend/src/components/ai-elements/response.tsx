"use client"

import { type ComponentProps, memo } from "react"
import { Streamdown } from "streamdown"
import { sanitizeMarkdownContent } from "@/lib/sanitize-markdown"
import { cn } from "@/lib/utils"

type ResponseProps = ComponentProps<typeof Streamdown>

export const Response = memo(
  ({ className, children, ...props }: ResponseProps) => (
    <Streamdown
      className={cn(
        "size-full [&>*:first-child]:mt-0 [&>*:last-child]:mb-0",
        className
      )}
      {...(typeof children === "string"
        ? { children: sanitizeMarkdownContent(children) }
        : { children })}
      {...props}
    />
  ),
  (prevProps, nextProps) => prevProps.children === nextProps.children
)

Response.displayName = "Response"
