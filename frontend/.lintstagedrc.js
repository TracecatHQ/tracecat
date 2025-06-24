module.exports = {
  "*.{js,jsx,ts,tsx,json,css,md}": [
    "biome check --write --files-ignore-unknown=true",
  ],
}
