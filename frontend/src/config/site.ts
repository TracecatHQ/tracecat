import { SiteConfig } from "@/types"

import { env } from "@/env.mjs"

export const siteConfig: SiteConfig = {
  name: "Tracecat",
  author: "Tracecat",
  description: "The AI-native Tines / Splunk SOAR alternative.",
  keywords: ["Next.js", "React", "Tailwind CSS", "Radix UI", "shadcn/ui"],
  url: {
    base: env.NEXT_PUBLIC_APP_URL,
    author: "Tracecat",
  },
  links: {
    github: "https://github.com/TracecatHQ/tracecat",
    discord: "https://discord.gg/n3GF4qxFU8",
    docs: "https://docs.tracecat.com",
  },
  ogImage: `${env.NEXT_PUBLIC_APP_URL}/og.jpg`,
}

export const routeConfig = {
  home: "/workspaces",
}
