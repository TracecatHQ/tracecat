# Tracecat docs

This directory contains the docs site published at `docs.tracecat.com`.

## Local preview

Install the docs preview CLI:

```bash
npm i -g mint
```

Start the local preview from this directory:

```bash
mint dev
```

The preview runs at `http://localhost:3000`.

## Generated docs

Some pages are generated from source metadata. Regenerate them when you change the underlying registry or reference data:

```bash
just gen-mcp-docs
just gen-tool-docs
```

## Troubleshooting

- If the preview fails to start, run `mint update`.
- If a page returns 404, make sure it is registered in `docs/docs.json`.
