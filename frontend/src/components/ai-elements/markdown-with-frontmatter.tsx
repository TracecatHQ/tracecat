"use client"

import { type ComponentProps, memo } from "react"
import { Streamdown } from "streamdown"
import {
  extractMarkdownFrontmatter,
  formatFrontmatterLabel,
  stringifyFrontmatterValue,
} from "@/lib/markdown-frontmatter"

type MarkdownWithFrontmatterProps = ComponentProps<typeof Streamdown>

/**
 * Renders markdown content while promoting YAML frontmatter into a metadata panel.
 */
export const MarkdownWithFrontmatter = memo(
  function MarkdownWithFrontmatterInner({
    children,
    className,
    ...props
  }: MarkdownWithFrontmatterProps) {
    if (typeof children !== "string") {
      return (
        <Streamdown {...props} className={className}>
          {children}
        </Streamdown>
      )
    }

    const parsed = extractMarkdownFrontmatter(children)
    if (!parsed) {
      return (
        <Streamdown {...props} className={className}>
          {children}
        </Streamdown>
      )
    }

    const metadataEntries = Object.entries(parsed.data).filter(
      ([key]) => key !== "title" && key !== "description"
    )
    const hasBody = parsed.body.trim().length > 0

    return (
      <div className="space-y-4">
        <section className="overflow-hidden rounded-lg border border-border/70 bg-muted/25">
          <div className="space-y-4 px-4 py-3">
            {(parsed.title || parsed.description) && (
              <header className="space-y-1">
                {parsed.title && (
                  <h2 className="text-base font-semibold text-foreground">
                    {parsed.title}
                  </h2>
                )}
                {parsed.description && (
                  <p className="text-sm text-muted-foreground">
                    {parsed.description}
                  </p>
                )}
              </header>
            )}

            {metadataEntries.length > 0 && (
              <dl className="grid gap-x-4 gap-y-3 sm:grid-cols-[140px_minmax(0,1fr)]">
                {metadataEntries.map(([key, value]) => (
                  <MetadataRow
                    key={key}
                    label={formatFrontmatterLabel(key)}
                    value={value}
                  />
                ))}
              </dl>
            )}
          </div>

          <details className="border-t border-border/70 px-4 py-3">
            <summary className="cursor-pointer text-xs font-medium text-muted-foreground">
              Raw frontmatter
            </summary>
            <pre className="mt-3 overflow-x-auto rounded-md border border-border/70 bg-background/80 p-3 text-xs leading-5 text-muted-foreground">
              {parsed.raw}
            </pre>
          </details>
        </section>

        {hasBody && (
          <Streamdown {...props} className={className}>
            {parsed.body}
          </Streamdown>
        )}
      </div>
    )
  }
)

MarkdownWithFrontmatter.displayName = "MarkdownWithFrontmatter"

function MetadataRow({ label, value }: { label: string; value: unknown }) {
  return (
    <>
      <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </dt>
      <dd className="min-w-0 text-sm text-foreground">
        {renderMetadataValue(value)}
      </dd>
    </>
  )
}

function renderMetadataValue(value: unknown) {
  if (value === null) {
    return <span className="text-muted-foreground">null</span>
  }

  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return <span className="break-words">{String(value)}</span>
  }

  if (isInlineArray(value)) {
    return (
      <div className="flex flex-wrap gap-1.5">
        {value.map((item, index) => (
          <span
            key={`${String(item)}-${index}`}
            className="rounded-md border border-border/70 bg-background/80 px-2 py-0.5 text-xs text-foreground"
          >
            {item === null ? "null" : String(item)}
          </span>
        ))}
      </div>
    )
  }

  return (
    <pre className="overflow-x-auto rounded-md border border-border/70 bg-background/80 p-3 text-xs leading-5 text-muted-foreground">
      {stringifyFrontmatterValue(value)}
    </pre>
  )
}

function isInlineArray(
  value: unknown
): value is Array<string | number | boolean | null> {
  return (
    Array.isArray(value) &&
    value.every(
      (item) =>
        item === null ||
        typeof item === "string" ||
        typeof item === "number" ||
        typeof item === "boolean"
    )
  )
}
