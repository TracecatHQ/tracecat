import { env } from "next-runtime-env"

export const authConfig = {
  disabled: ["1", "true"].includes(env("NEXT_PUBLIC_AUTH_DISABLED") || "false"),
  authType: env("NEXT_PUBLIC_AUTH_TYPE") || "basic",
  staleTime: 5 * 60 * 1000, // 5 minutes
}
