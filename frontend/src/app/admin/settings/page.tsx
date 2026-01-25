"use client"

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"

export default function AdminSettingsPage() {
  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">
          Platform settings
        </h1>
        <p className="text-muted-foreground">
          Configure global platform settings.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>General settings</CardTitle>
          <CardDescription>
            Platform-wide configuration options.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Platform settings configuration coming soon.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
