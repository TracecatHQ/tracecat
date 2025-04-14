import { CenteredSpinner } from "@/components/loading/spinner"

export default function Loading() {
  return (
    <div className="container flex h-full max-w-[800px] flex-col justify-center space-y-2 p-16">
      <CenteredSpinner />
    </div>
  )
}
