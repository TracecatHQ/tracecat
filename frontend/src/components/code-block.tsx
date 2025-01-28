import React from "react"

export function CodeBlock({
  title,
  children,
}: {
  title?: string
  children: React.ReactNode
}) {
  return (
    <div className="space-y-2">
      {title && (
        <span className="text-xs font-semibold text-foreground/50">
          {title}
        </span>
      )}
      <pre className="flex flex-col overflow-auto text-wrap rounded-md border bg-muted-foreground/5 p-4 font-mono text-foreground/70">
        {children}
      </pre>
    </div>
  )
}
