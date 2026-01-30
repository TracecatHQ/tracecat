import { InfoCircledIcon } from "@radix-ui/react-icons"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"

export function ProtectedColumnsAlert() {
  return (
    <Alert>
      <InfoCircledIcon className="size-4" />
      <AlertTitle>Protected columns</AlertTitle>
      <AlertDescription>
        Columns named <code>id</code>, <code>created_at</code>, or{" "}
        <code>updated_at</code> are protected column names and will be skipped
        during import.
      </AlertDescription>
    </Alert>
  )
}
