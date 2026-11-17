// jest.config.js
module.exports = {
  setupFilesAfterEnv: ["<rootDir>/jest.setup.js"],
  testEnvironment: "jsdom", // For React components
  transform: {
    "^.+\\.tsx?$": [
      "ts-jest",
      {
        tsconfig: {
          jsx: "react-jsx",
        },
      },
    ],
    // The AI SDK ships ESM only, so its dist files need a CJS transform.
    "^.+\\.m?js$": [
      "ts-jest",
      { tsconfig: { allowJs: true, jsx: "react-jsx", module: "commonjs" } },
    ],
  },
  testPathIgnorePatterns: ["/node_modules/", "/.next/", "/tests/smoke/"],
  transformIgnorePatterns: [
    "/node_modules/(?!(\\.pnpm|yaml|ai|@ai-sdk|@shadcn|@workflow)/)",
  ],
  moduleNameMapper: {
    "^@/(.*)$": "<rootDir>/src/$1",
    "^lucide-react/dynamicIconImports$":
      "<rootDir>/tests/mocks/lucide-dynamic-icon-imports.js",
    // Mock CSS and other non-JS files
    "\\.(css|less|scss|sass)$": "identity-obj-proxy",
    "\\.(jpg|jpeg|png|gif|eot|otf|webp|svg|ttf|woff|woff2|mp4|webm|wav|mp3|m4a|aac|oga)$":
      "jest-transform-stub",
    // jsdom resolves the "default" export condition, which points at yaml's
    // ESM browser build. Force the CJS build so Jest can require it.
    "^yaml$": "<rootDir>/node_modules/yaml/dist/index.js",
  },
}
