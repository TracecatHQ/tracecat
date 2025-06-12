import inspect
import re
from typing import Union, get_type_hints

from fastapi import APIRouter, HTTPException, status
from lark import Lark, LarkError, Token, Tree
from lark.visitors import Interpreter
from pydantic import BaseModel

from tracecat.auth.credentials import RoleACL
from tracecat.db.dependencies import AsyncDBSession
from tracecat.editor.models import EditorActionRead, EditorFunctionRead, EditorParamRead
from tracecat.expressions.functions import FUNCTION_MAPPING
from tracecat.expressions.parser.grammar import grammar
from tracecat.identifiers.workflow import AnyWorkflowIDQuery
from tracecat.registry.fields import EditorComponent
from tracecat.types.auth import Role
from tracecat.workflow.management.management import WorkflowsManagementService

router = APIRouter(prefix="/editor", tags=["editor"])


# LSP Models for template expression validation
class ExpressionValidationRequest(BaseModel):
    expression: str


class ValidationError(BaseModel):
    message: str
    line: int
    column: int


class SyntaxToken(BaseModel):
    type: str
    value: str
    start: int
    end: int


class ExpressionValidationResponse(BaseModel):
    is_valid: bool
    errors: list[ValidationError] = []
    tokens: list[SyntaxToken] = []


# Token visitor to extract syntax highlighting information
class TokenExtractor(Interpreter):
    def __init__(self):
        self.tokens: list[SyntaxToken] = []
        self.position = 0

    def visit(self, tree):
        if isinstance(tree, Token):
            # Map Lark token types to our highlighting types
            token_type = self._map_token_type(tree.type)
            self.tokens.append(
                SyntaxToken(
                    type=token_type,
                    value=str(tree.value),
                    start=self.position,
                    end=self.position + len(str(tree.value)),
                )
            )
            self.position += len(str(tree.value))
        elif isinstance(tree, Tree):
            # Handle tree nodes
            for child in tree.children:
                self.visit(child)

    def _map_token_type(self, lark_type: str) -> str:
        """Map Lark grammar token types to frontend highlighting types"""
        type_mapping = {
            # Keywords from grammar
            "ACTIONS": "keyword",
            "SECRETS": "keyword",
            "INPUTS": "keyword",
            "ENV": "keyword",
            "TRIGGER": "keyword",
            "FN": "keyword",
            "var": "keyword",
            "for": "keyword",
            "in": "keyword",
            "if": "keyword",
            "else": "keyword",
            "inputs": "keyword",
            "steps": "keyword",
            # Literals
            "STRING_LITERAL": "string",
            "NUMERIC_LITERAL": "number",
            "BOOL_LITERAL": "bool",
            "NONE_LITERAL": "keyword",
            # Operators
            "OPERATOR": "operator",
            # Type specifiers
            "TYPE_SPECIFIER": "keyword",
            # Identifiers and paths
            "CNAME": "variableName",
            "ATTRIBUTE_PATH": "propertyName",
            "PARTIAL_JSONPATH_EXPR": "propertyName",
            "FN_NAME_WITH_TRANSFORM": "function",
            # Punctuation
            "(": "bracket",
            ")": "bracket",
            "[": "bracket",
            "]": "bracket",
            "{": "bracket",
            "}": "bracket",
            ",": "punctuation",
            ".": "punctuation",
            ":": "punctuation",
            "->": "operator",
        }

        return type_mapping.get(lark_type, "punctuation")


# Initialize the Lark parser
try:
    expression_parser = Lark(grammar, start="root", parser="lalr")
except Exception as e:
    print(f"Failed to initialize expression parser: {e}")
    expression_parser = None


@router.get("/functions", response_model=list[EditorFunctionRead])
async def list_functions(
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
):
    functions = []

    for name, func in FUNCTION_MAPPING.items():
        # Get function signature
        sig = inspect.signature(func)
        doc = inspect.getdoc(func) or ""
        type_hints = get_type_hints(func)

        # Extract parameter information
        parameters = []
        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue

            param_type_hint = type_hints.get(param_name, "Any")

            def format_type(type_hint) -> str:
                if hasattr(type_hint, "__origin__") and type_hint.__origin__ is Union:
                    return " | ".join(format_type(t) for t in type_hint.__args__)
                # Handle generic types like list[str], dict[str, int], etc.
                elif hasattr(type_hint, "__origin__"):
                    args = ", ".join(format_type(arg) for arg in type_hint.__args__)
                    return f"{type_hint.__origin__.__name__}[{args}]"
                # Handle basic types
                return getattr(type_hint, "__name__", str(type_hint))

            param_type = format_type(param_type_hint)
            parameters.append(
                EditorParamRead(
                    name=param_name,
                    type=param_type,
                    optional=param.default != inspect.Parameter.empty,
                )
            )

        # Update return type handling
        return_type = type_hints.get("return", "Any")
        return_type_str = format_type(return_type)

        functions.append(
            EditorFunctionRead(
                name=name,
                description=doc,
                parameters=parameters,
                return_type=return_type_str,
            )
        )

    return functions


@router.get("/actions", response_model=list[EditorActionRead])
async def list_actions(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDQuery,
):
    # find actions that are in the workflow
    service = WorkflowsManagementService(session, role=role)
    workflow = await service.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )

    actions = workflow.actions
    if not actions:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No actions found in workflow",
        )

    return [
        EditorActionRead(
            type=action.type, ref=action.ref, description=action.description
        )
        for action in actions
    ]


@router.post("/expressions/validate", response_model=ExpressionValidationResponse)
async def validate_expression(
    request: ExpressionValidationRequest,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
):
    """
    LSP endpoint for validating template expressions using the Lark grammar.

    This endpoint provides syntax validation and token information for syntax highlighting
    of template expressions like: ACTIONS.step1.result, SECRETS.api_key, etc.
    """
    if not expression_parser:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Expression parser not initialized",
        )

    expression = request.expression.strip()
    if not expression:
        return ExpressionValidationResponse(is_valid=True, errors=[], tokens=[])

    try:
        # Parse the expression using Lark
        parse_tree = expression_parser.parse(expression)

        # Extract tokens for syntax highlighting
        token_extractor = TokenExtractor()
        token_extractor.visit(parse_tree)

        return ExpressionValidationResponse(
            is_valid=True, errors=[], tokens=token_extractor.tokens
        )

    except LarkError as e:
        # Parse the error to extract line and column information
        error_msg = str(e)
        line = 1
        column = 1

        # Try to extract position information from error message
        try:
            # Some Lark errors include position info in the message
            pos_match = re.search(r"at line (\d+), column (\d+)", error_msg)
            if pos_match:
                line = int(pos_match.group(1))
                column = int(pos_match.group(2))
        except Exception:
            # Fallback to default position
            pass

        # Clean up error message for user display
        if "Unexpected token" in error_msg:
            error_msg = "Unexpected token in expression"
        elif "Expected" in error_msg:
            error_msg = "Invalid syntax in expression"
        else:
            error_msg = "Syntax error in expression"

        return ExpressionValidationResponse(
            is_valid=False,
            errors=[ValidationError(message=error_msg, line=line, column=column)],
            tokens=[],
        )

    except Exception as e:
        # Handle any other parsing errors
        return ExpressionValidationResponse(
            is_valid=False,
            errors=[
                ValidationError(message=f"Parser error: {str(e)}", line=1, column=1)
            ],
            tokens=[],
        )


@router.get("/field-schema", response_model=EditorComponent)
def field_schema():
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Not implemented",
    )
