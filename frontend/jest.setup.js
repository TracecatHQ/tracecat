// Import jest-dom extensions
require("@testing-library/jest-dom")

// Add TextEncoder/TextDecoder polyfills for jsdom
global.TextEncoder = require("util").TextEncoder
global.TextDecoder = require("util").TextDecoder

// Mock next-runtime-env
jest.mock("next-runtime-env", () => ({
  env: jest.fn((key) => process.env[key] || ""),
}))

// Alternatively,
// pnpm add -D dotenv
// require("dotenv").config()
