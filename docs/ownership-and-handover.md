# Ownership and handover guide

RVV Miniputt should be operable by the club/region, not by one person's personal accounts. This guide is the operational ownership inventory, migration checklist, annual access review, and emergency recovery procedure for handing the service to another volunteer.

The codebase can document and enforce safe boundaries, but the actual transfer of external accounts is **MANUAL**: only authorized club administrators can change GitHub organizations, Microsoft 365 assets, WordPress/Spond roles, domains, credentials, or payment/renewal contacts.

## Ownership principles

- Use club-controlled identities: organization accounts, shared mailboxes, service principals, GitHub teams, and named club volunteers.
- Keep at least two club-authorized owners or a written recovery path for every critical dependency.
- Grant least privilege by role; avoid broad administrator access for routine operation.
- Store secrets in managed secret stores, never in personal files, local shell history, generated public output, or repository content.
- Separate generation, review, publication, and rollback responsibilities where possible.
- Review access annually and whenever the season coordinator, primary maintainer, or club IT contact changes.

## Operator role matrix

| Role | Routine responsibility | GitHub permissions | Microsoft 365 permissions | WordPress permissions | Spond permissions | Notes |
|---|---|---|---|---|---|---|
| Season operator | Runs validation, review-bundle generation, and non-public dry runs. | Read/write access to run manual Actions; no Pages environment approval by default. | Read access to registrations/workbooks; write access only to approved input workbook area. | None unless posting schedule links. | Read schedule/group setup where needed. | Can run `make operator-run`, `make publish-preview`, and validation workflows. |
| Season approver | Reviews final schedule and authorizes publication. | Approval access to the protected `pages-publication` environment. | Read access to final workbook and review packets. | Editor access if the public site/news post is updated manually. | Admin or event-publisher rights if importing events. | Should not need repository admin rights. |
| Calendar/source custodian | Maintains calendar credentials and recovery inputs. | Read/write Actions if running source recovery; no release or environment admin. | Access only to source-specific credential vault entries or service-account rotation process. | None. | None unless Spond is also a source. | Responsible for stale/blocked source follow-up. |
| Technical maintainer | Maintains code, CI, release automation, and emergency fixes. | Repository maintainer/admin, branch-protection management, secrets management. | Access to service principals/automation connections as needed. | Admin only if the site integration is code-managed. | Admin only if API/import integration requires it. | At least two named maintainers should exist. |
| Club/region owner | Owns the service and appoints volunteers. | Organization owner or documented recovery contact. | Microsoft 365 owner/global admin or delegated SharePoint/Forms owner. | Site owner/admin. | Group owner/admin. | Holds the emergency recovery authority. |

## Critical ownership inventory

Use this table as the live checklist during setup, handover, and annual review. Fill in the named accounts in the club's private operations record; do not commit private email addresses, passwords, tokens, or recovery codes to this repository.

| Dependency | Current owner/account type | Desired club-owned owner | Backup owner or recovery path | Required permissions | Secrets and rotation | Renewal/recovery procedure | Remove or automate? |
|---|---|---|---|---|---|---|---|
| GitHub repository | MANUAL: record whether it is personal, club organization, or vendor-owned. | Club/region GitHub organization. | Two organization owners plus GitHub account-recovery method. | Maintainer for code, triage/write for operators, protected environment approvers for publishing. | GitHub Actions secrets/environments only; rotate when a maintainer leaves. | Transfer repository to org or document why it remains personal; export admin list annually. | Automate routine runs through Actions and Make targets. |
| GitHub Pages / `gh-pages` | MANUAL: record current Pages publisher. | Same club GitHub organization/repository. | Protected `pages-publication` environment with at least two approvers. | Publish workflow can update Pages branch; approvers authorize public writes. | Use environment secrets if credentials are needed; do not store tokens in files. | Verify public URL after every publish; keep rollback procedure documented. | Already automated by `operator publish`/rollback workflows. |
| GitHub teams, roles, and protected environments | MANUAL. | Club organization teams such as `season-operators`, `season-approvers`, `maintainers`. | Organization owners. | Least privilege per role matrix. | Environment secrets managed by maintainers only. | Review team membership annually and after volunteer changes. | Automate policy checks where GitHub supports it. |
| Microsoft Forms registrations | MANUAL. | Club Microsoft 365 group/shared form owner. | Second form owner plus M365 admin recovery. | Form owner/editor for coordinators; response reader for import operators. | No tokens in repo; export connections use managed Power Automate/M365 connections. | Confirm ownership before each signup season; export a backup of schema/questions. | Prefer export/import automation via approved workflow. |
| SharePoint site, Lists, and Excel workbooks | MANUAL. | Club SharePoint site or Team. | Second site owner plus M365 admin recovery. | Operators need read/write only to input and review libraries they operate. | M365-managed permissions; no local personal workbook paths in automation. | Maintain version history and recover deleted workbooks through SharePoint recycle bin. | Automate canonical workbook export where safe. |
| Power Automate flows and connections | MANUAL. | Club-owned service account or solution owned by club M365 environment. | Co-owner plus M365 admin recovery for connections. | Flow editor for automation maintainer; run-only/user connections where possible. | Store connector credentials in M365 connections/service principals; rotate on owner changes. | Export solution package annually and after major flow changes. | Automate registration-to-input conversion once reviewed. |
| Spond group administration | MANUAL. | Club/team Spond group owner/admin. | At least one backup Spond admin. | Event import/publish permission for season approver/operator. | Store API/import credentials outside repo; rotate when admins change. | Confirm backup admin before season start; document import rollback/delete procedure. | Use Spond Excel exports/imports instead of personal manual copy/paste. |
| WordPress administrator/editor accounts | MANUAL. | Club-controlled WordPress administrator account. | Second administrator plus hosting-provider recovery. | Editor for posting links; admin only for plugin/theme/user changes. | Password manager or hosting secret store; rotate admin passwords/tokens annually. | Document provider login, backup restore, and DNS mapping privately. | Automate publishing links only after approval. |
| Calendar-source credentials and service accounts | MANUAL. | Club-owned service accounts or delegated calendar owners. | Backup custodian plus provider recovery path. | Read-only calendar access unless edits are explicitly needed. | GitHub environment secrets, M365/Google secret stores, or a club password manager; never `BOOKUP_EMAIL`/`BOOKUP_PASSWORD` in prompts/logs. | Rotate credentials at least annually and immediately after volunteer departure. | Prefer public/ical read-only feeds where available. |
| Domains, DNS, analytics, and notification addresses | MANUAL. | Club/region registrar account, DNS zone, analytics property, and shared mailbox. | Second owner and registrar recovery contact. | Publish/technical maintainers need only the DNS or analytics permissions they operate. | Registrar MFA and recovery codes in club password manager/offline safe. | Record renewal dates, payment owner, and emergency transfer process privately. | Use shared notification addresses for alerts and form replies. |

## Managed secrets and rotation

1. Keep repository content secret-free. Configuration committed to Git should contain placeholders, public URLs, or references to secret names only.
2. Use the narrowest managed store available:
   - GitHub Actions environment secrets for publish/release automation.
   - Microsoft 365/Power Automate connections or service principals for Forms/SharePoint automation.
   - A club password manager for recovery credentials, backup codes, registrar access, WordPress admin access, and Spond admin credentials.
3. Rotate secrets:
   - before every season if they grant publication, source access, or administrator privileges;
   - immediately when a volunteer with access leaves;
   - after any suspected exposure in logs, prompts, artifacts, or exported files.
4. Verify generated public output and logs do not contain personal paths, emails, usernames, tokens, or local machine names before publication.

## Handover procedure

1. **Inventory:** copy the critical ownership inventory into the private club operations record and fill in actual owner names/accounts.
2. **Transfer or justify:** move each dependency to the desired club-owned owner, or document why it cannot yet move and the exact recovery path.
3. **Create backup ownership:** add at least one backup owner/admin/approver before removing any personal-account dependency.
4. **Restrict routine access:** map volunteers to the operator role matrix and remove unnecessary admin access.
5. **Rotate secrets:** rotate all credentials that were ever available to the outgoing maintainer or stored outside managed secret stores.
6. **Run the dry run:** a second person performs the end-to-end dry run below without borrowing the original maintainer's login.
7. **Record evidence:** store completion evidence in the private club operations record, not in this public repository.

## Annual access review checklist

Perform this review before season signup opens and again during formal handover.

- [ ] GitHub organization/repository owners, maintainers, teams, branch protections, and protected environments are current.
- [ ] Microsoft Forms, SharePoint sites/libraries, Lists, workbooks, and Power Automate flows have at least two club-authorized owners.
- [ ] Spond and WordPress admin/editor lists match the current volunteer roster.
- [ ] Calendar-source credentials are still needed, stored in managed secret stores, and rotated according to policy.
- [ ] Domains, DNS, analytics, notification mailboxes, and renewals have club-owned contacts and backup recovery.
- [ ] Generated public output and automation config are free of personal paths, emails, usernames, and machine-specific assumptions.
- [ ] A non-maintainer has completed the second-person dry run since the last role change.

## Emergency recovery when the primary maintainer is unavailable

1. Club/region owner confirms authority to recover the service.
2. Use organization/provider recovery to access GitHub, Microsoft 365, WordPress/Spond, DNS, and secret stores; do not request or reuse the maintainer's personal account.
3. Freeze public publication until a season approver and technical maintainer confirm current exports and Pages target.
4. Rotate all publication, calendar-source, automation, and admin credentials the unavailable maintainer may have held.
5. Run `make status`, `make questions-all`, and `make publish-preview` to inspect current pipeline state without publishing.
6. If a published schedule must be reverted, use `make publish-history` and `make rollback RUN_ID=<id> CONFIRM_PUBLIC=1` after protected-environment approval.
7. Record what was recovered, what was rotated, and which access gaps remain in the private operations record.

## Second-person end-to-end dry run

A volunteer who is not the original maintainer should be able to complete this checklist using only club-approved access:

- [ ] Open the repository and manual GitHub Actions workflows.
- [ ] Retrieve or upload the current approved input workbook from the club-owned SharePoint/Forms process.
- [ ] Run validation through `make check`, `make operator-run`, or the browser validation workflow.
- [ ] Inspect source health and answer any pending operator questions with the documented authority for that role.
- [ ] Generate a review bundle without public publication.
- [ ] Confirm the publication preview and privacy report do not contain personal paths, emails, usernames, or secrets.
- [ ] Obtain approval from a season approver.
- [ ] Publish or rehearse publication through the protected workflow/process.
- [ ] Verify the public URL or rollback a test publish using the documented rollback procedure.
- [ ] Record blockers and update this guide if any step still depends on the original maintainer.

## Private operations record template

Keep this template in the club's private documentation system, not in the repository:

```text
Dependency:
Current owner/account:
Desired club-owned owner:
Backup owner:
Role permissions granted:
Secret store/reference name:
Last rotation date:
Renewal date/payment owner:
Recovery procedure link/location:
Automation/removal opportunity:
Last reviewed by/date:
Dry-run evidence:
```
