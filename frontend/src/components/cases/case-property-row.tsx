import type { ReactNode } from "react"

interface CasePropertyRowProps {
  label: string
  value: ReactNode
}

export function CasePropertyRow({ label, value }: CasePropertyRowProps) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-xs text-muted-foreground">{label}</span>
      {value}
    </div>
  )
}
