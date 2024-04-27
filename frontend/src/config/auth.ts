export const authConfig = {
  disabled: ["1", "true"].includes(
    process.env.NEXT_PUBLIC_DISABLE_AUTH || "false"
  ),
}
