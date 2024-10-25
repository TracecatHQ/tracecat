import { env } from "next-runtime-env"

export const authConfig = {
  authTypes: (env("NEXT_PUBLIC_AUTH_TYPES") || "basic,google_oauth")
    .split(",")
    .map((x) => x.toLowerCase()),
  staleTime: 5 * 60 * 1000, // 5 minutes
}
