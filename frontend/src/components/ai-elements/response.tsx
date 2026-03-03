"use client"

import { type ComponentProps, memo } from "react"
import { Streamdown } from "streamdown"
import {
  SAFE_MARKDOWN_IMAGE_PREFIXES,
  SAFE_MARKDOWN_LINK_PREFIXES,
  sanitizeMarkdownContent,
} from "@/lib/sanitize-markdown"
import { cn } from "@/lib/utils"

type ResponseProps = ComponentProps<typeof Streamdown>

export const Response = memo(
  ({ className, children, ...props }: ResponseProps) => (
    <Streamdown
      className={cn(
        "size-full [&>*:first-child]:mt-0 [&>*:last-child]:mb-0",
        className
      )}
      allowedImagePrefixes={SAFE_MARKDOWN_IMAGE_PREFIXES}
      allowedLinkPrefixes={SAFE_MARKDOWN_LINK_PREFIXES}
      {...(typeof children === "string"
        ? { children: sanitizeMarkdownContent(children) }
        : { children })}
      {...props}
    />
  ),
  (prevProps, nextProps) => prevProps.children === nextProps.children
)

Response.displayName = "Response"
