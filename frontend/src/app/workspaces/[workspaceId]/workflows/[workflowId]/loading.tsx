import { CenteredSpinner } from "@/components/loading/spinner"

export default function Loading() {
  return (
    <div className="flex size-full items-center justify-center">
      <CenteredSpinner />
    </div>
  )
}
