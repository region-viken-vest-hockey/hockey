# Engineering principles

This repository supports a volunteer-maintained hockey planning project with low traffic, limited usage, and limited maintainer time. Prefer pragmatic, boring solutions that are easy to understand, operate, debug, and hand over.

## Default decision rule

Choose the simplest solution that satisfies the current requirements.

Before introducing a new service, framework, abstraction, or workflow, first ask whether the existing pipeline or tools can be extended instead.

## Prefer

- Existing repository scripts and command wrappers
- Small, deterministic Python scripts
- Make targets where they simplify repeatable local operations
- GitHub Actions for validation, generation, and publishing
- Power Automate for simple Microsoft 365 integration and file transfer
- SharePoint, Microsoft Forms, Excel, CSV, and repository files where they are sufficient
- Incremental improvements over rewrites
- Narrowly scoped credentials, such as a fine-grained PAT limited to one repository

## Avoid by default

Do not introduce these unless a demonstrated current requirement makes the simpler approach insufficient:

- Microservices
- Message queues
- Custom authentication or token services
- GitHub Apps for simple single-repository automation
- Cloud functions used only to bridge otherwise simple integrations
- Kubernetes or container orchestration
- Databases where files are sufficient
- Abstraction, extensibility, or scalability for hypothetical future needs

## Quality priorities

Optimize for:

1. Correctness
2. Deterministic and reproducible behavior
3. Readability
4. Easy debugging
5. Low operational burden
6. Volunteer maintainability

Theoretical scale and enterprise completeness are lower priorities unless the repository clearly demonstrates that they are needed.

## Code style

No formatter or linter existed before `scripts/check lint`/`scripts/check file-length` were introduced (ruff, `E`/`F` rules only, plus a custom file-length check); there is no other codified style beyond the principles on this page and the conventions already visible in the code.

### File size

Keep a Python file under `tournament_scheduler/` at or under 300 lines. A file that grows past that is usually doing more than one thing and should be split along SOLID lines (single responsibility per module) rather than accreting further.

This is enforced by `scripts/check_file_length.py` (`scripts/check file-length`) as a ratchet, not a rewrite mandate: files already over 300 lines when the check was introduced are grandfathered in `scripts/file-length-baseline.txt` at their line count *at that time*, and may not grow past it, but are not required to shrink on their own schedule. Any file not in that baseline (new, or already compliant) must stay at or under 300 lines. Split a file rather than editing the baseline to raise its allowance.

## Expected operating context

Assume, unless the repository shows otherwise:

- A handful of maintainers
- Tens or hundreds of users
- Low traffic
- Infrequent updates
- No dedicated operations team
- Limited time available for maintenance

Explain material trade-offs, but recommend the proportionate solution for this operating context by default.
