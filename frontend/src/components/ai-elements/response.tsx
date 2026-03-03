"use client"

import { type ComponentProps, memo } from "react"
import { Streamdown } from "streamdown"
import {
  ALLOWED_MARKDOWN_IMAGE_PREFIXES,
  ALLOWED_MARKDOWN_LINK_PREFIXES,
  getStreamdownRehypePlugins,
} from "@/lib/sanitize-markdown"
import { cn } from "@/lib/utils"

type ResponseProps = ComponentProps<typeof Streamdown>

const responseRehypePlugins = getStreamdownRehypePlugins() as NonNullable<
  ResponseProps["rehypePlugins"]
>

export const Response = memo(
  ({ className, children, ...props }: ResponseProps) => (
    <Streamdown
      {...props}
      className={cn(
        "size-full [&>*:first-child]:mt-0 [&>*:last-child]:mb-0",
        className
      )}
      allowedImagePrefixes={ALLOWED_MARKDOWN_IMAGE_PREFIXES}
      allowedLinkPrefixes={ALLOWED_MARKDOWN_LINK_PREFIXES}
      rehypePlugins={responseRehypePlugins}
    >
      {children}
    </Streamdown>
  ),
  (prevProps, nextProps) => prevProps.children === nextProps.children
)

Response.displayName = "Response"
