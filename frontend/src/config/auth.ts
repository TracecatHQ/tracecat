import { env } from "next-runtime-env"

export const authConfig = {
  authType: env("NEXT_PUBLIC_AUTH_TYPE") || "basic",
  staleTime: 5 * 60 * 1000, // 5 minutes
}
