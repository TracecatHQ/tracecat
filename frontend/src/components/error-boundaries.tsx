"use client"

import type React from "react"
import {
  BaseErrorBoundary,
  type ErrorBoundaryProps,
  type ErrorInfo,
  FormFieldErrorFallback,
  MinimalErrorFallback,
  SectionErrorFallback,
} from "@/components/error-boundary"
import { Input } from "@/components/ui/input"

// Component Error Boundary - for individual components
export function ComponentErrorBoundary({
  children,
  onError,
  ...props
}: ErrorBoundaryProps) {
  const handleError = (error: Error, errorInfo: ErrorInfo) => {
    // Log component-specific error
    console.warn("Component Error Boundary:", error.message)
    onError?.(error, errorInfo)
  }

  return (
    <BaseErrorBoundary
      {...props}
      fallback={MinimalErrorFallback}
      onError={handleError}
    >
      {children}
    </BaseErrorBoundary>
  )
}

// Section Error Boundary - for logical sections like tabs, forms
export function SectionErrorBoundary({
  children,
  onError,
  ...props
}: ErrorBoundaryProps) {
  const handleError = (error: Error, errorInfo: ErrorInfo) => {
    // Log section-specific error
    console.warn("Section Error Boundary:", error.message)
    onError?.(error, errorInfo)
  }

  return (
    <BaseErrorBoundary
      {...props}
      fallback={SectionErrorFallback}
      onError={handleError}
    >
      {children}
    </BaseErrorBoundary>
  )
}

// Form Field Error Boundary - for form inputs with graceful degradation
export function FormFieldErrorBoundary({
  children,
  onError,
  fallbackInput,
  ...props
}: ErrorBoundaryProps & { fallbackInput?: React.ReactNode }) {
  const handleError = (error: Error, errorInfo: ErrorInfo) => {
    // Log form field error
    console.warn("Form Field Error Boundary:", error.message)
    onError?.(error, errorInfo)
  }

  // Custom fallback that includes a simple input option
  const FormFieldFallback = ({
    error,
    resetError,
    errorId,
  }: {
    error: Error | null
    resetError: () => void
    errorId: string
  }) => (
    <FormFieldErrorFallback
      error={error}
      resetError={resetError}
      errorId={errorId}
      errorInfo={null}
    >
      {fallbackInput || (
        <Input placeholder="Simplified text input (error mode)" />
      )}
    </FormFieldErrorFallback>
  )

  return (
    <BaseErrorBoundary
      {...props}
      fallback={FormFieldFallback}
      onError={handleError}
    >
      {children}
    </BaseErrorBoundary>
  )
}

// Expression Error Boundary - specifically for template expressions
export function ExpressionErrorBoundary({
  children,
  onError,
  fieldName,
  value,
  onChange,
  fallbackInput,
  ...props
}: ErrorBoundaryProps & {
  fieldName?: string
  value?: string
  onChange?: (value: string) => void
  fallbackInput?: React.ReactNode
}) {
  const handleError = (error: Error, errorInfo: ErrorInfo) => {
    // Log expression-specific error with context
    console.warn(`Expression Error Boundary [${fieldName}]:`, error.message)
    onError?.(error, errorInfo)
  }

  // Custom fallback that provides a simple text input
  const ExpressionFallback = ({
    error,
    resetError,
    errorId,
  }: {
    error: Error | null
    resetError: () => void
    errorId: string
  }) => (
    <FormFieldErrorFallback
      error={error}
      resetError={resetError}
      errorId={errorId}
      errorInfo={null}
    >
      {fallbackInput}
    </FormFieldErrorFallback>
  )

  return (
    <BaseErrorBoundary
      {...props}
      fallback={ExpressionFallback}
      onError={handleError}
    >
      {children}
    </BaseErrorBoundary>
  )
}

// Workflow Builder Error Boundary - top-level protection
export function WorkflowBuilderErrorBoundary({
  children,
  onError,
  ...props
}: ErrorBoundaryProps) {
  const handleError = (error: Error, errorInfo: ErrorInfo) => {
    // Log workflow builder error - this is serious
    console.error("Workflow Builder Error Boundary:", error.message)

    // Could send to error reporting service here
    onError?.(error, errorInfo)
  }

  return (
    <BaseErrorBoundary {...props} onError={handleError}>
      {children}
    </BaseErrorBoundary>
  )
}

// Template Pills Error Boundary - for CodeMirror template pill rendering
export function TemplatePillErrorBoundary({
  children,
  onError,
  ...props
}: ErrorBoundaryProps) {
  const handleError = (error: Error, errorInfo: ErrorInfo) => {
    // Log template pill error
    console.warn("Template Pill Error Boundary:", error.message)
    onError?.(error, errorInfo)
  }

  // Minimal fallback for template pills to not disrupt editor flow
  const TemplatePillFallback = ({
    error,
  }: {
    error: Error | null
    resetError: () => void
  }) => (
    <span
      className="cm-template-pill cm-template-error"
      title={`Error: ${error?.message}`}
    >
      [Error]
    </span>
  )

  return (
    <BaseErrorBoundary
      {...props}
      fallback={TemplatePillFallback}
      onError={handleError}
    >
      {children}
    </BaseErrorBoundary>
  )
}
