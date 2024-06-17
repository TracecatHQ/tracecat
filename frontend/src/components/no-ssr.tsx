import React from "react"
import dynamic from "next/dynamic"

const NoSSRWrapper = (props: React.HTMLAttributes<HTMLDivElement>) => (
  <>{props.children}</>
)
export default dynamic(() => Promise.resolve(NoSSRWrapper), {
  ssr: false,
})
