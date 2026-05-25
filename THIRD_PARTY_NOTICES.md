# Third-party notices

Replay Agent Recorder is released under the MIT License. The optional React/XYFlow graph viewer bundles JavaScript dependencies into `replay/xyflow_assets/` when `npm run build:xyflow-viewer` is run.

Before publishing a release artifact, verify the installed dependency tree and update this file if versions or licenses change.

## Bundled viewer dependency families

The viewer source under `viewer/` is built with packages declared in `package.json`, including:

| Package | Purpose |
|---|---|
| `@xyflow/react` | React Flow / XYFlow graph rendering. |
| `@dagrejs/dagre` | Graph layout support. |
| `react` | UI library used by the viewer. |
| `react-dom` | React DOM renderer. |
| `vite` | Frontend build tool. |
| `@vitejs/plugin-react` | Vite React plugin. |
| `typescript` | TypeScript compiler and language tooling. |

## Maintainer checklist

When preparing a public release:

```bash
npm install
npm ls --all
npm run build:xyflow-viewer
```

Then confirm the current licenses for bundled packages and transitive dependencies. If your release process generates a machine-readable license report, place it next to this file or link it from here.

Do not copy third-party source code or license text into this file unless you have verified the exact package version and license requirements.
