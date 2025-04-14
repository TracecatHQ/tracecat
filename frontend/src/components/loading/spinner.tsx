import { cn } from "@/lib/utils"

export function CenteredSpinner() {
  return (
    <div className="container flex h-full max-w-[800px] flex-col justify-center space-y-2 p-16">
      <div className="relative mx-auto size-6">
        <Spinner />
      </div>
    </div>
  )
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg className={cn(className)} viewBox="0 0 50 50">
      {/* Darker, slightly translucent ring */}
      <circle
        cx="25"
        cy="25"
        r="20"
        fill="none"
        stroke="#d1d5db"
        strokeWidth="5"
        strokeOpacity="0.7"
      />
      {/* Lighter spinning segment with rounded caps */}
      <circle
        cx="25"
        cy="25"
        r="20"
        fill="none"
        stroke="#6b7280"
        strokeWidth="5"
        strokeLinecap="round"
        strokeDasharray="30 100"
        className="origin-center animate-spin"
        style={{ transformOrigin: "center" }}
      />
    </svg>
  )
}

export function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="container flex h-full max-w-[800px] flex-col justify-center space-y-2 p-16 text-xs text-muted-foreground">
      {children}
    </div>
  )
}
