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
    "[\\\\/]marked[\\\\/].+\\.js$": [
      "ts-jest",
      {
        tsconfig: {
          allowJs: true,
        },
      },
    ],
  },
  testPathIgnorePatterns: ["/node_modules/", "/.next/", "/tests/smoke/"],
  // Skip transforming node_modules, except for `marked` and `yaml`, which ship
  // ESM only (marked's UMD build is not reachable through its exports map, so
  // it is unusable under CJS). Matching on `node_modules/<pkg>/` anywhere in
  // the path keeps this independent of the installer's layout, which nests the
  // real package under `.pnpm/<pkg>@<version>/` but hoists it under npm, yarn,
  // and pnpm's `node-linker=hoisted`.
  transformIgnorePatterns: [
    "^(?!.*node_modules/(?:marked|yaml)/).*node_modules/",
  ],
  moduleNameMapper: {
    "^@/(.*)$": "<rootDir>/src/$1",
    "^lucide-react/dynamicIconImports$":
      "<rootDir>/tests/mocks/lucide-dynamic-icon-imports.js",
    // Mock CSS and other non-JS files
    "\\.(css|less|scss|sass)$": "identity-obj-proxy",
    "\\.(jpg|jpeg|png|gif|eot|otf|webp|svg|ttf|woff|woff2|mp4|webm|wav|mp3|m4a|aac|oga)$":
      "jest-transform-stub",
    // Mock yaml module for Jest
    "^yaml$": "identity-obj-proxy",
  },
}
