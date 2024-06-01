import { JSONSchema7 } from "json-schema"

import {
  generateSchemaDefault,
  transformJsonSchemaToTableRows,
} from "@/lib/jsonschema"

const jsonSchema: JSONSchema7 = {
  properties: {
    url: {
      description: "The destination of the HTTP request",
      format: "uri",
      maxLength: 100,
      minLength: 1,
      title: "Url",
      type: "string",
    },
    headers: {
      additionalProperties: {
        type: "string",
      },
      default: null,
      description: "HTTP request headers",
      title: "Headers",
      type: "object",
    },
    payload: {
      anyOf: [
        {
          type: "object",
        },
        {
          items: {},
          type: "array",
        },
      ],
      default: null,
      description: "HTTP request payload",
      title: "Payload",
    },
    params: {
      default: null,
      description: "URL query parameters",
      title: "Params",
      type: "object",
    },
    method: {
      default: "GET",
      description: "HTTP reqest method",
      enum: ["GET", "POST", "PUT", "DELETE"],
      title: "Method",
      type: "string",
    },
  },
  required: ["url"],
  title: "CoreHttpRequest",
  type: "object",
}

describe("transformJsonSchemaToTableRows", () => {
  it("should transform a JSON schema to table rows", () => {
    const rows = transformJsonSchemaToTableRows(jsonSchema)
    expect(rows).toEqual([
      {
        parameter: "url *",
        type: "string",
        default: "-",
        description: "The destination of the HTTP request",
        constraints: "format: uri\nmaxLength: 100\nminLength: 1",
      },
      {
        parameter: "headers",
        type: "object",
        default: "-",
        description: "HTTP request headers",
        constraints: "additionalProperties: string",
      },
      {
        parameter: "payload",
        type: "object | array",
        default: "-",
        description: "HTTP request payload",
        constraints: "",
      },
      {
        parameter: "params",
        type: "object",
        default: "-",
        description: "URL query parameters",
        constraints: "",
      },
      {
        parameter: "method",
        type: "string",
        default: "GET",
        description: "HTTP reqest method",
        constraints: "enum: [GET, POST, PUT, DELETE]",
      },
    ])
  })
})

describe("generateDefaultObject", () => {
  it("should generate a default object from a JSON schema", () => {
    const obj = generateSchemaDefault(jsonSchema)
    console.warn(obj)
    expect(obj).toEqual({
      url: "",
      headers: {},
      payload: {},
      params: {},
      method: "GET",
    })
  })
})
