import { redirect } from "next/navigation"

/** SSH keys moved to the custom registry Repository page. */
export default function SSHKeysPage() {
  return redirect("/organization/settings/custom-registry")
}
