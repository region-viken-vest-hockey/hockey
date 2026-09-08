Publish the most recently generated RVV Miniputt export to GitHub Pages. Never runs or reruns the pipeline.

Use:

```bash
scripts/rvv-miniputt operator publish --confirm-public <user-args>
```

Before publishing, confirm `.pipeline/stage4_export.json` exists and its `errors` list is empty; if not, stop and tell the user to run `/rvv-miniputt run` first instead of running the pipeline yourself. Do not run `/rvv-miniputt publish` in the shell.

Report which export bundle was published, the published URL, any verification warning, and any publish failure.
