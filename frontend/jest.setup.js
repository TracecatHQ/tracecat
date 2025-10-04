// Import jest-dom extensions
require("@testing-library/jest-dom")

// Add TextEncoder/TextDecoder polyfills for jsdom
global.TextEncoder = require("util").TextEncoder
global.TextDecoder = require("util").TextDecoder

// Add Web Streams API polyfills for AI SDK
const {
  TransformStream,
  ReadableStream,
  WritableStream,
} = require("node:stream/web")
global.TransformStream = TransformStream
global.ReadableStream = ReadableStream
global.WritableStream = WritableStream

// Mock next-runtime-env
jest.mock("next-runtime-env", () => ({
  env: jest.fn((key) => process.env[key] || ""),
}))

// Mock use-stick-to-bottom (UI utility)
jest.mock("use-stick-to-bottom", () => {
  const StickToBottom = ({ children }) => children
  StickToBottom.Content = ({ children }) => children
  return {
    StickToBottom,
    useStickToBottomContext: () => ({
      isAtBottom: true,
      scrollToBottom: jest.fn(),
    }),
  }
})

// Mock nanoid
jest.mock("nanoid", () => ({
  nanoid: () => "test-id",
}))

// Mock react-markdown
jest.mock("react-markdown", () => ({
  __esModule: true,
  default: ({ children }) => children,
}))

// Mock streamdown
jest.mock("streamdown", () => ({
  Streamdown: ({ children }) => children,
}))

// Mock react-syntax-highlighter
jest.mock("react-syntax-highlighter", () => ({
  Prism: ({ children }) => children,
  PrismLight: ({ children }) => children,
}))

jest.mock("react-syntax-highlighter/dist/esm/styles/prism", () => ({
  oneDark: {},
  oneLight: {},
}))

// Mock motion/react
jest.mock("motion/react", () => {
  const React = require("react")
  return {
    motion: {
      div: ({ children, ...props }) =>
        React.createElement("div", props, children),
      button: ({ children, ...props }) =>
        React.createElement("button", props, children),
    },
    AnimatePresence: ({ children }) => children,
  }
})

// Alternatively,
// pnpm add -D dotenv
// require("dotenv").config()
