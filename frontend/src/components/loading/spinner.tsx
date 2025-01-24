import { Loader2 } from "lucide-react"

export function CenteredSpinner() {
  return (
    <div className="container flex h-full max-w-[800px] flex-col justify-center space-y-2 p-16">
      <Loader2 className="mx-auto animate-spin text-gray-500" />
    </div>
  )
}

export function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="container flex h-full max-w-[800px] flex-col justify-center space-y-2 p-16 text-xs text-muted-foreground">
      {children}
    </div>
  )
}
