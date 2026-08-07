// jest.config.js
module.exports = {
  setupFilesAfterEnv: ["<rootDir>/jest.setup.js"],
  testEnvironment: "jsdom", // For React components
  transform: {
    // `.js`/`.mjs` are included because `marked` (pulled in by
    // @tiptap/markdown) ships ESM only and has to be downleveled to CommonJS
    // before Jest can require it. ts-jest only honours one set of options per
    // run, so this must stay a single entry.
    "^.+\\.m?[jt]sx?$": [
      "ts-jest",
      {
        tsconfig: {
          jsx: "react-jsx",
          allowJs: true,
        },
      },
    ],
  },
  testPathIgnorePatterns: ["/node_modules/", "/.next/", "/tests/smoke/"],
  // The optional `.pnpm/` hop makes this work with pnpm's nested store layout,
  // where every real package path contains `node_modules/.pnpm/<name>@<ver>/`.
  transformIgnorePatterns: [
    "node_modules/(?!(\\.pnpm/)?(yaml|marked|react-hotkeys-hook)([@/]|$))",
  ],
  moduleNameMapper: {
    // Only the first matching pattern applies, so the asset mocks have to come
    // before the `@/` alias: stylesheets are imported through it too.
    "\\.(css|less|scss|sass)$": "identity-obj-proxy",
    "\\.(jpg|jpeg|png|gif|eot|otf|webp|svg|ttf|woff|woff2|mp4|webm|wav|mp3|m4a|aac|oga)$":
      "jest-transform-stub",
    "^@/(.*)$": "<rootDir>/src/$1",
    "^lucide-react/dynamicIconImports$":
      "<rootDir>/tests/mocks/lucide-dynamic-icon-imports.js",
    // Mock yaml module for Jest
    "^yaml$": "identity-obj-proxy",
  },
}
