"use client"

import { AlertTriangleIcon, RefreshCwIcon } from "lucide-react"
import React, { Component, type ReactNode } from "react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

// Base error boundary types
export interface ErrorInfo {
  componentStack: string
  errorBoundary?: string
  errorBoundaryStack?: string
}

export interface ErrorBoundaryState {
  hasError: boolean
  error: Error | null
  errorInfo: ErrorInfo | null
  errorId: string
}

export interface ErrorBoundaryProps {
  children: ReactNode
  fallback?: React.ComponentType<ErrorFallbackProps>
  onError?: (error: Error, errorInfo: ErrorInfo) => void
  isolateError?: boolean
}

export interface ErrorFallbackProps {
  error: Error | null
  errorInfo: ErrorInfo | null
  resetError: () => void
  errorId: string
}

// Base Error Boundary Component
export class BaseErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  private resetTimeoutId: number | null = null

  constructor(props: ErrorBoundaryProps) {
    super(props)
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
      errorId: "",
    }
  }

  static getDerivedStateFromError(error: Error): Partial<ErrorBoundaryState> {
    return {
      hasError: true,
      error,
      errorId: Math.random().toString(36).substring(7),
    }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    this.setState({
      errorInfo,
    })

    // Call custom error handler if provided
    this.props.onError?.(error, errorInfo)

    // Log error for debugging
    console.group(`🚨 Error Boundary Caught Error [${this.state.errorId}]`)
    console.error("Error:", error)
    console.error("Error Info:", errorInfo)
    console.groupEnd()
  }

  componentWillUnmount() {
    if (this.resetTimeoutId) {
      window.clearTimeout(this.resetTimeoutId)
    }
  }

  resetError = () => {
    if (this.resetTimeoutId) {
      window.clearTimeout(this.resetTimeoutId)
    }

    this.setState({
      hasError: false,
      error: null,
      errorInfo: null,
      errorId: "",
    })
  }

  render() {
    if (this.state.hasError) {
      const Fallback = this.props.fallback || DefaultErrorFallback
      return (
        <Fallback
          error={this.state.error}
          errorInfo={this.state.errorInfo}
          resetError={this.resetError}
          errorId={this.state.errorId}
        />
      )
    }

    return this.props.children
  }
}

// Default Error Fallback Component
export function DefaultErrorFallback({
  error,
  errorInfo,
  resetError,
  errorId,
}: ErrorFallbackProps) {
  const [showDetails, setShowDetails] = React.useState(false)

  return (
    <Alert variant="destructive" className="m-4">
      <AlertTriangleIcon className="size-4" />
      <AlertTitle>Something went wrong</AlertTitle>
      <AlertDescription className="space-y-3">
        <p className="text-sm">
          An unexpected error occurred. You can try to reload this section or
          continue with your work.
        </p>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={resetError}
            className="h-8"
          >
            <RefreshCwIcon className="mr-2 size-3" />
            Retry
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowDetails(!showDetails)}
            className="h-8"
          >
            {showDetails ? "Hide" : "Show"} Details
          </Button>
        </div>
        {showDetails && (
          <Card className="mt-4">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Error Details</CardTitle>
            </CardHeader>
            <CardContent className="text-xs">
              <div className="space-y-2">
                <div>
                  <span className="font-medium">Error ID:</span> {errorId}
                </div>
                <div>
                  <span className="font-medium">Message:</span> {error?.message}
                </div>
                {error?.stack && (
                  <div>
                    <span className="font-medium">Stack:</span>
                    <pre className="mt-1 whitespace-pre-wrap rounded bg-muted p-2 text-xs">
                      {error.stack}
                    </pre>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        )}
      </AlertDescription>
    </Alert>
  )
}

// Minimal Error Fallback for tight spaces
export function MinimalErrorFallback({
  error,
  resetError,
  errorId,
}: ErrorFallbackProps) {
  return (
    <div className="flex items-center gap-2 rounded border border-destructive/20 bg-destructive/10 p-2 text-xs text-destructive">
      <AlertTriangleIcon className="size-3 shrink-0" />
      <span className="flex-1 truncate">Error occurred</span>
      <Button
        variant="ghost"
        size="sm"
        onClick={resetError}
        className="size-6 p-0"
        title="Retry"
      >
        <RefreshCwIcon className="size-3" />
      </Button>
    </div>
  )
}

// Inline Error Fallback for form fields
export function InlineErrorFallback({
  error,
  resetError,
  errorId,
}: ErrorFallbackProps) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 rounded border border-destructive/20 bg-destructive/5 p-2 text-xs text-destructive">
        <AlertTriangleIcon className="size-3" />
        <span className="flex-1">Component error: {error?.message}</span>
        <Button
          variant="ghost"
          size="sm"
          onClick={resetError}
          className="h-6 px-2 text-xs"
        >
          Retry
        </Button>
      </div>
    </div>
  )
}

// Section Error Fallback for larger sections
export function SectionErrorFallback({
  error,
  resetError,
  errorId,
}: ErrorFallbackProps) {
  return (
    <div className="rounded-md border border-destructive/20 bg-destructive/5 p-4">
      <div className="flex items-start gap-3">
        <AlertTriangleIcon className="mt-0.5 size-5 text-destructive" />
        <div className="flex-1 space-y-3">
          <div>
            <h4 className="text-sm font-medium text-destructive">
              Section temporarily unavailable
            </h4>
            <p className="mt-1 text-xs text-muted-foreground">
              This section encountered an error but the rest of your workflow is
              still functional.
            </p>
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={resetError}
              className="h-8"
            >
              <RefreshCwIcon className="mr-2 size-3" />
              Reload Section
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

// Form Field Error Fallback with graceful degradation
export function FormFieldErrorFallback({
  error,
  resetError,
  errorId,
  children,
}: ErrorFallbackProps & { children?: ReactNode }) {
  const [fallbackMode, setFallbackMode] = React.useState<"error" | "text">(
    "error"
  )

  if (fallbackMode === "text") {
    return (
      <div className="space-y-2">
        <div className="rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-600">
          Field is in simplified mode due to an error.
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setFallbackMode("error")}
            className="ml-2 h-6 px-2 text-xs"
          >
            Show Error
          </Button>
        </div>
        {children}
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 rounded border border-destructive/20 bg-destructive/5 p-2 text-xs text-destructive">
        <AlertTriangleIcon className="size-3" />
        <span className="flex-1">Field error: {error?.message}</span>
        <Button
          variant="ghost"
          size="sm"
          onClick={resetError}
          className="h-6 px-2 text-xs"
        >
          Retry
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setFallbackMode("text")}
          className="h-6 px-2 text-xs"
        >
          Use Simple Mode
        </Button>
      </div>
    </div>
  )
}
