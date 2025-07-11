import type React from "react"

export function SectionHead({
  icon,
  text,
}: {
  icon: React.ReactNode
  text: string
}) {
  return (
    <div className="flex w-full justify-start p-2 text-center text-xs font-semibold">
      {icon}
      <span>{text}</span>
    </div>
  )
}
