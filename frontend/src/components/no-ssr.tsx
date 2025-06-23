import dynamic from "next/dynamic"
import type React from "react"

const NoSSRWrapper = (props: React.HTMLAttributes<HTMLDivElement>) => (
  <>{props.children}</>
)
export default dynamic(() => Promise.resolve(NoSSRWrapper), {
  ssr: false,
})
