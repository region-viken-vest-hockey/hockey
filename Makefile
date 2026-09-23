# RVV Miniputt — human operator menu
#
# This Makefile is intentionally a thin adapter. Recipes delegate to the
# canonical repository scripts/CLI entrypoints and keep safety decisions in
# those commands instead of reimplementing workflow logic here.

SHELL := /bin/sh
.DEFAULT_GOAL := help

ROOT_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
RVV ?= $(ROOT_DIR)/scripts/rvv-miniputt
ACTIVITY_INPUT ?= $(ROOT_DIR)/Årshjul for aktiviteter.xlsx
CHECK ?= $(ROOT_DIR)/scripts/check
RELEASE ?= $(ROOT_DIR)/scripts/release
BOOTSTRAP ?= $(ROOT_DIR)/scripts/bootstrap
INSTALL ?= $(ROOT_DIR)/scripts/install.sh
PYTHON ?= $(ROOT_DIR)/venv/bin/python3
SECRET_SCAN ?= $(ROOT_DIR)/scripts/secret-scan.sh
RULES_REPORT ?= $(ROOT_DIR)/scripts/rules-report.sh
KAMPVEILEDER_CONVERT ?= $(ROOT_DIR)/scripts/convert-kampveileder.sh

export ID ANSWER SCOPE SCOPE_KEY RUN_ID TAG CONFIRM_PUBLIC CONFIRM_CLEANUP CSV ARGS BACKEND RESULT_FILE

PUBLIC_TARGETS := help bootstrap install check test dependency-lock secret-scan rules-report rule-catalog kampveileder-markdown \
	operator-run operator-run-force run status logs calendars calendars-refresh sources-status \
	waiver \
	aktivitetskalender aktivitetskalender-publish registered-teams registered-teams-publish \
	questions questions-all answer promote \
	audit-context audit-evidence audit-run audit-submit \
	publish-preview publish verify-publish publish-history rollback \
	cleanup-exports cleanup-exports-apply \
	release-dry-run release

.PHONY: $(PUBLIC_TARGETS) all

all: help

help:
	@echo "RVV Miniputt operator targets (default: help)"
	@echo ""
	@echo "Setup and verification:"
	@echo "  make bootstrap                     Set up a new machine/checkout with Python 3.12"
	@echo "  make install                       Reinstall Python/project dependencies"
	@echo "  make check [ARGS='...']            Run canonical verification via scripts/check"
	@echo "  make test [ARGS='...']             Run pytest directly for local iteration"
	@echo "  make dependency-lock               Verify requirements.lock is fresh"
	@echo "  make secret-scan                   Run repository secret scan"
	@echo "  make rules-report                  Regenerate/check scheduler rules report"
	@echo "  make rule-catalog                  Regenerate/check canonical scheduling rule catalog"
	@echo "  make kampveileder-markdown         Convert NIHF Kampveileder PDF to generated Markdown"
	@echo ""
	@echo "Planning and inspection (ARGS is appended to the underlying CLI command):"
	@echo "  make operator-run [ARGS='...']     scripts/rvv-miniputt operator run"
	@echo "  make operator-run-force            operator run --force"
	@echo "  make run [ARGS='...']              scripts/rvv-miniputt run"
	@echo "  make status [ARGS='...']           scripts/rvv-miniputt status"
	@echo "  make logs [ARGS='...']             scripts/rvv-miniputt logs list"
	@echo "  make calendars [ARGS='...']        scripts/rvv-miniputt calendars"
	@echo "  make calendars-refresh             calendars --refresh"
	@echo "  make sources-status [ARGS='...']   sources status"
	@echo "  make waiver ARGS='list|create|revoke ...'"
	@echo "                                      Operator-only, audited hard-rule exception (never agent-created)"
	@echo "  make aktivitetskalender [ARGS='...']"
	@echo "                                      Regenerate activities/ from Årshjul workbook"
	@echo "  make aktivitetskalender-publish CONFIRM_PUBLIC=1 [ARGS='...']"
	@echo "                                      Regenerate activities/ and publish full Pages snapshot"
	@echo "  make registered-teams CSV=downloads/Miniputt-26-27.csv [ARGS='...']"
	@echo "                                      Regenerate registered-teams/ Påmeldte lag page"
	@echo "  make registered-teams-publish CSV=downloads/Miniputt-26-27.csv CONFIRM_PUBLIC=1 [ARGS='...']"
	@echo "                                      Regenerate Påmeldte lag and publish full Pages snapshot"
	@echo ""
	@echo "Human decisions (operator supplies judgment; Make only records it):"
	@echo "  make questions                     List pending operator questions"
	@echo "  make questions-all                 Include answered/stale questions"
	@echo "  make answer ID=<id> ANSWER='<text>'"
	@echo "  make promote ID=<id> SCOPE=workspace [SCOPE_KEY=<key>]"
	@echo ""
	@echo "Semantic safety-net audit (issue #325 — required before publish):"
	@echo "  make audit-context                 Print evidence inventory for the harness to review"
	@echo "  make audit-evidence ARGS='--item 2'"
	@echo "                                      Retrieve detailed evidence for a selector"
	@echo "  make audit-run BACKEND=<name>      Headless audit (claude|openai|llm_bridge)"
	@echo "  make audit-submit RESULT_FILE=<path>"
	@echo "                                      Submit a structured audit verdict"
	@echo ""
	@echo "GitHub Pages publication and recovery:"
	@echo "  make publish-preview [ARGS='...']  Non-mutating sanitized publish preview"
	@echo "  make publish CONFIRM_PUBLIC=1      Publish with CLI confirmation safeguards"
	@echo "  make verify-publish                Verify latest published bundle"
	@echo "  make publish-history               List publish/rollback history"
	@echo "  make rollback RUN_ID=<id> CONFIRM_PUBLIC=1"
	@echo ""
	@echo "Export housekeeping:"
	@echo "  make cleanup-exports                Dry-run safe cleanup of proven superseded exports"
	@echo "  make cleanup-exports-apply CONFIRM_CLEANUP=1"
	@echo "                                      Delete only lifecycle-verified superseded exports"
	@echo ""
	@echo "Guarded release:"
	@echo "  make release-dry-run TAG=vX.Y.Z    Validate release without tag/push"
	@echo "  make release TAG=vX.Y.Z            Guarded annotated tag release"
	@echo ""
	@echo "Safety: help/all/run/operator-run never publish publicly. Mutating publish, rollback,"
	@echo "release paths retain explicit target-specific safeguards."

bootstrap:
	@cd "$(ROOT_DIR)" && "$(BOOTSTRAP)" $(ARGS)

install:
	@cd "$(ROOT_DIR)" && sh "$(INSTALL)" $(ARGS)

check:
	@cd "$(ROOT_DIR)" && "$(CHECK)" $(ARGS)

test:
	@cd "$(ROOT_DIR)" && "$(PYTHON)" -m pytest $(ARGS)

dependency-lock:
	@cd "$(ROOT_DIR)" && "$(CHECK)" dependency-lock

secret-scan:
	@cd "$(ROOT_DIR)" && sh "$(SECRET_SCAN)" $(ARGS)

rules-report:
	@cd "$(ROOT_DIR)" && sh "$(RULES_REPORT)" $(ARGS)

rule-catalog:
	@cd "$(ROOT_DIR)" && "$(PYTHON)" scripts/render-rule-catalog.py $(ARGS)

kampveileder-markdown:
	@cd "$(ROOT_DIR)" && sh "$(KAMPVEILEDER_CONVERT)" $(ARGS)

operator-run:
	@cd "$(ROOT_DIR)" && "$(RVV)" operator run $(ARGS)

operator-run-force:
	@cd "$(ROOT_DIR)" && "$(RVV)" operator run --force $(ARGS)

run:
	@cd "$(ROOT_DIR)" && "$(RVV)" run $(ARGS)


status:
	@cd "$(ROOT_DIR)" && "$(RVV)" status $(ARGS)

logs:
	@cd "$(ROOT_DIR)" && "$(RVV)" logs list $(ARGS)

calendars:
	@cd "$(ROOT_DIR)" && "$(RVV)" calendars $(ARGS)

calendars-refresh:
	@cd "$(ROOT_DIR)" && "$(RVV)" calendars --refresh $(ARGS)


sources-status:
	@cd "$(ROOT_DIR)" && "$(RVV)" sources status $(ARGS)

waiver:
	@cd "$(ROOT_DIR)" && "$(RVV)" waiver $(ARGS)

aktivitetskalender:
	@cd "$(ROOT_DIR)" && "$(RVV)" activities --input "$(ACTIVITY_INPUT)" $(ARGS)

aktivitetskalender-publish:
	@if [ "$${CONFIRM_PUBLIC:-}" != "1" ]; then echo "ERROR: make aktivitetskalender-publish requires CONFIRM_PUBLIC=1" >&2; exit 2; fi
	@cd "$(ROOT_DIR)" && "$(RVV)" activities --input "$(ACTIVITY_INPUT)" --publish --confirm-public $(ARGS)

registered-teams:
	@if [ -z "$${CSV:-}" ]; then echo "ERROR: make registered-teams requires CSV=<sharepoint-export.csv>" >&2; exit 2; fi
	@cd "$(ROOT_DIR)" && "$(RVV)" registered-teams --csv "$$CSV" $(ARGS)

registered-teams-publish:
	@if [ -z "$${CSV:-}" ]; then echo "ERROR: make registered-teams-publish requires CSV=<sharepoint-export.csv>" >&2; exit 2; fi
	@if [ "$${CONFIRM_PUBLIC:-}" != "1" ]; then echo "ERROR: make registered-teams-publish requires CONFIRM_PUBLIC=1" >&2; exit 2; fi
	@cd "$(ROOT_DIR)" && "$(RVV)" registered-teams --csv "$$CSV" --publish --confirm-public $(ARGS)

questions:
	@cd "$(ROOT_DIR)" && "$(RVV)" operator questions $(ARGS)

questions-all:
	@cd "$(ROOT_DIR)" && "$(RVV)" operator questions --all $(ARGS)

answer:
	@if [ -z "$${ID:-}" ]; then echo "ERROR: make answer requires ID=<question-id>" >&2; exit 2; fi
	@if [ -z "$${ANSWER:-}" ]; then echo "ERROR: make answer requires ANSWER='<answer>'" >&2; exit 2; fi
	@cd "$(ROOT_DIR)" && "$(RVV)" operator answer "$$ID" "$$ANSWER" $(ARGS)

promote:
	@if [ -z "$${ID:-}" ]; then echo "ERROR: make promote requires ID=<question-id>" >&2; exit 2; fi
	@if [ -z "$${SCOPE:-}" ]; then echo "ERROR: make promote requires SCOPE=input_version|season|workspace" >&2; exit 2; fi
	@cd "$(ROOT_DIR)" && if [ -n "$${SCOPE_KEY:-}" ]; then \
		"$(RVV)" operator promote --scope-key "$$SCOPE_KEY" "$$ID" "$$SCOPE" $(ARGS); \
	else \
		"$(RVV)" operator promote "$$ID" "$$SCOPE" $(ARGS); \
	fi

audit-context:
	@cd "$(ROOT_DIR)" && "$(RVV)" operator audit-context $(ARGS)

audit-evidence:
	@cd "$(ROOT_DIR)" && "$(RVV)" operator audit-evidence $(ARGS)

audit-run:
	@if [ -z "$${BACKEND:-}" ]; then echo "ERROR: make audit-run requires BACKEND=claude|openai|llm_bridge" >&2; exit 2; fi
	@cd "$(ROOT_DIR)" && "$(RVV)" operator audit-run --backend "$$BACKEND" $(ARGS)

audit-submit:
	@if [ -z "$${RESULT_FILE:-}" ]; then echo "ERROR: make audit-submit requires RESULT_FILE=<path>" >&2; exit 2; fi
	@cd "$(ROOT_DIR)" && "$(RVV)" operator audit-submit --result-file "$$RESULT_FILE" $(ARGS)

publish-preview:
	@cd "$(ROOT_DIR)" && "$(RVV)" operator publish --dry-run $(ARGS)

publish:
	@if [ "$${CONFIRM_PUBLIC:-}" != "1" ]; then echo "ERROR: make publish requires CONFIRM_PUBLIC=1" >&2; exit 2; fi
	@cd "$(ROOT_DIR)" && "$(RVV)" operator publish --confirm-public $(ARGS)

verify-publish:
	@cd "$(ROOT_DIR)" && "$(RVV)" operator verify $(ARGS)

publish-history:
	@cd "$(ROOT_DIR)" && "$(RVV)" operator publish-history $(ARGS)

rollback:
	@if [ -z "$${RUN_ID:-}" ]; then echo "ERROR: make rollback requires RUN_ID=<published-run-id>" >&2; exit 2; fi
	@if [ "$${CONFIRM_PUBLIC:-}" != "1" ]; then echo "ERROR: make rollback requires CONFIRM_PUBLIC=1" >&2; exit 2; fi
	@cd "$(ROOT_DIR)" && "$(RVV)" operator rollback "$$RUN_ID" --confirm-public $(ARGS)


cleanup-exports:
	@cd "$(ROOT_DIR)" && "$(ROOT_DIR)/scripts/cleanup-exports" $(ARGS)

cleanup-exports-apply:
	@if [ "${CONFIRM_CLEANUP:-}" != "1" ]; then echo "ERROR: make cleanup-exports-apply requires CONFIRM_CLEANUP=1" >&2; exit 2; fi
	@cd "$(ROOT_DIR)" && "$(ROOT_DIR)/scripts/cleanup-exports" --apply $(ARGS)

release-dry-run:
	@if [ -z "$${TAG:-}" ]; then echo "ERROR: make release-dry-run requires TAG=vX.Y.Z" >&2; exit 2; fi
	@cd "$(ROOT_DIR)" && "$(RELEASE)" --dry-run "$$TAG" $(ARGS)

release:
	@if [ -z "$${TAG:-}" ]; then echo "ERROR: make release requires TAG=vX.Y.Z" >&2; exit 2; fi
	@cd "$(ROOT_DIR)" && "$(RELEASE)" "$$TAG" $(ARGS)
