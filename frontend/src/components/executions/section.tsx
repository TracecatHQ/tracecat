import type React from "react"

export function SectionHead({
  icon,
  text,
}: {
  icon: React.ReactNode
  text: string
}) {
  return (
    <div className="flex h-[33px] w-full items-center justify-start gap-2 border-b px-3 text-left text-xs font-semibold">
      {icon}
      <span>{text}</span>
    </div>
  )
}
