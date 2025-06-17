/**
 * Common CodeMirror and Template Expression Utilities
 *
 * This module contains shared functionality for template expression editing,
 * including pill rendering, validation, completion, and interaction logic.
 */
import React from "react"
import { EditorActionRead, EditorFunctionRead } from "@/client"
import { createRoot } from "react-dom/client"

import {
  actionCache,
  functionCache,
  TemplateExpressionValidation,
} from "@/components/editor/codemirror/common"

function createTooltipContentJSX(
  info: { validation: TemplateExpressionValidation; innerContent: string },
  workspaceId: string,
  workflowId: string | null
): React.ReactElement {
  let func: EditorFunctionRead | undefined
  let action: EditorActionRead | undefined

  const fnMatch = info.innerContent.match(/^FN\.(\w+)/)
  if (fnMatch) {
    const functionName = fnMatch[1]
    const cachedFunctions = functionCache.get(workspaceId)
    if (cachedFunctions) {
      func = cachedFunctions.find((f) => f.name === functionName)
    }
  }

  const actionMatch = info.innerContent.match(/^ACTIONS\.(\w+)/)
  if (actionMatch && workflowId) {
    const actionRef = actionMatch[1]
    const cacheKey = `${workspaceId}-${workflowId}`
    const cachedActions = actionCache.get(cacheKey)
    if (cachedActions) {
      action = cachedActions.find((a) => a.ref === actionRef)
    }
  }

  return (
    <TemplateTooltip
      innerContent={info.innerContent}
      validation={info.validation}
      func={func}
      action={action}
    />
  )
}

// JSX Components for tooltip content
interface FunctionTooltipProps {
  func: EditorFunctionRead
}

function FunctionTooltip({ func }: FunctionTooltipProps) {
  const parameterList = func.parameters
    .map((p) => `${p.name}: ${p.type}`)
    .join(", ")

  return (
    <div className="function-completion-info">
      <div className="function-signature">
        {func.name}({parameterList}) → {func.return_type}
      </div>
      {func.description && (
        <div className="function-description">{func.description}</div>
      )}
      {func.parameters && func.parameters.length > 0 && (
        <>
          <div className="function-params-title">Parameters:</div>
          {func.parameters.map((param, index) => (
            <div key={index} className="function-param">
              <strong>{param.name}</strong>
              {param.optional && " (optional)"}: <em>{param.type}</em>
            </div>
          ))}
        </>
      )}
    </div>
  )
}

interface ActionTooltipProps {
  action: EditorActionRead
}

function ActionTooltip({ action }: ActionTooltipProps) {
  const commonProps = ["result", "error", "status"]

  return (
    <div className="action-completion-info">
      <div className="action-signature">
        {action.ref} ({action.type})
      </div>
      {action.description && (
        <div className="action-description">{action.description}</div>
      )}
      <div className="action-props-title">Available properties:</div>
      {commonProps.map((prop, index) => (
        <div key={index} className="action-prop">
          <strong>{prop}</strong>: Access {prop} from this action
        </div>
      ))}
    </div>
  )
}

interface TemplateTooltipProps {
  innerContent: string
  validation: TemplateExpressionValidation
  func?: EditorFunctionRead
  action?: EditorActionRead
}

function TemplateTooltip({
  innerContent,
  validation,
  func,
  action,
}: TemplateTooltipProps) {
  return (
    <div className="cm-template-expression-tooltip">
      <div className="cm-tooltip-header">
        Template Expression: {innerContent}
      </div>

      {func && <FunctionTooltip func={func} />}
      {action && <ActionTooltip action={action} />}

      <div
        className={`cm-tooltip-status ${validation.isValid ? "valid" : "invalid"}`}
      >
        {validation.isValid ? "✓ Valid" : "✗ Invalid"}
      </div>

      {validation.errors && validation.errors.length > 0 && (
        <div className="cm-tooltip-errors">
          <div className="cm-tooltip-section-title">Errors:</div>
          {validation.errors.map((error, index) => (
            <div key={index} className="cm-tooltip-error-item">
              • {error.msg}
            </div>
          ))}
        </div>
      )}

      {validation.tokens && validation.tokens.length > 0 && (
        <div className="cm-tooltip-tokens">
          <div className="cm-tooltip-section-title">Tokens:</div>
          <div className="cm-tooltip-tokens-list">
            {validation.tokens.map((token, index) => (
              <span
                key={index}
                className={`cm-tooltip-token cm-token-${token.type.toLowerCase()}`}
                title={`Type: ${token.type}`}
              >
                {token.value}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// Helper function to render JSX to DOM element for CodeMirror tooltips
function renderTooltipJSX(component: React.ReactElement): HTMLElement {
  try {
    const container = document.createElement("div")
    const root = createRoot(container)
    root.render(component)
    return container
  } catch (error) {
    throw error
  }
}
