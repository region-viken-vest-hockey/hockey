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
INSTALL ?= $(ROOT_DIR)/scripts/install.sh
SECRET_SCAN ?= $(ROOT_DIR)/scripts/secret-scan.sh
RULES_REPORT ?= $(ROOT_DIR)/scripts/rules-report.sh
PACKAGE_BACKEND_SH ?= $(ROOT_DIR)/scripts/package-desktop-backend.sh
PACKAGE_BACKEND_PS1 ?= $(ROOT_DIR)/scripts/package-desktop-backend.ps1
NPM ?= npm
POWERSHELL ?= powershell
DOTENVX ?= $(ROOT_DIR)/node_modules/.bin/dotenvx
DOTENVX_ENV_FILE ?= $(ROOT_DIR)/.env.bookup

export ID ANSWER SCOPE SCOPE_KEY RUN_ID TAG CONFIRM_PUBLIC CSV ARGS DOTENVX_ENV_FILE

PUBLIC_TARGETS := help install check test dependency-lock secret-scan rules-report \
	operator-run operator-run-force run run-dotenvx status logs calendars calendars-refresh calendars-refresh-dotenvx sources-status \
	aktivitetskalender aktivitetskalender-publish registered-teams registered-teams-publish \
	questions questions-all answer promote \
	publish-preview publish verify-publish publish-history rollback \
	desktop-start desktop-clean build-mac build-windows build-linux release-dry-run release

.PHONY: $(PUBLIC_TARGETS) all

all: help

help:
	@echo "RVV Miniputt operator targets (default: help)"
	@echo ""
	@echo "Setup and verification:"
	@echo "  make install                       Install Python/project dependencies"
	@echo "  make check [ARGS='...']            Run canonical verification via scripts/check"
	@echo "  make test [ARGS='...']             Run pytest directly for local iteration"
	@echo "  make dependency-lock               Verify requirements.lock is fresh"
	@echo "  make secret-scan                   Run repository secret scan"
	@echo "  make rules-report                  Regenerate/check scheduler rules report"
	@echo ""
	@echo "Planning and inspection (ARGS is appended to the underlying CLI command):"
	@echo "  make operator-run [ARGS='...']     scripts/rvv-miniputt operator run"
	@echo "  make operator-run-force            operator run --force"
	@echo "  make run [ARGS='...']              scripts/rvv-miniputt run"
	@echo "  make run-dotenvx [ARGS='...']      dotenvx run -f .env.bookup -- scripts/rvv-miniputt run"
	@echo "  make status [ARGS='...']           scripts/rvv-miniputt status"
	@echo "  make logs [ARGS='...']             scripts/rvv-miniputt logs list"
	@echo "  make calendars [ARGS='...']        scripts/rvv-miniputt calendars"
	@echo "  make calendars-refresh             calendars --refresh"
	@echo "  make calendars-refresh-dotenvx     dotenvx calendars --refresh using .env.bookup"
	@echo "  make sources-status [ARGS='...']   sources status"
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
	@echo "GitHub Pages publication and recovery:"
	@echo "  make publish-preview [ARGS='...']  Non-mutating sanitized publish preview"
	@echo "  make publish CONFIRM_PUBLIC=1      Publish with CLI confirmation safeguards"
	@echo "  make verify-publish                Verify latest published bundle"
	@echo "  make publish-history               List publish/rollback history"
	@echo "  make rollback RUN_ID=<id> CONFIRM_PUBLIC=1"
	@echo ""
	@echo "Desktop, cleanup, and guarded release:"
	@echo "  make desktop-start                 Start desktop supervisor prototype"
	@echo "  make desktop-clean                 Bounded desktop build cleanup"
	@echo "  make build-mac                     Build macOS .dmg/.zip"
	@echo "  make build-windows                 Build Windows installer"
	@echo "  make build-linux                   Build Linux AppImage"
	@echo "  make release-dry-run TAG=vX.Y.Z    Validate release without tag/push"
	@echo "  make release TAG=vX.Y.Z            Guarded annotated tag release"
	@echo ""
	@echo "Safety: help/all/run/operator-run never publish publicly. Mutating publish, rollback,"
	@echo "release, and cleanup paths retain explicit target-specific safeguards."

install:
	@cd "$(ROOT_DIR)" && sh "$(INSTALL)" $(ARGS)

check:
	@cd "$(ROOT_DIR)" && "$(CHECK)" $(ARGS)

test:
	@cd "$(ROOT_DIR)" && python3 -m pytest $(ARGS)

dependency-lock:
	@cd "$(ROOT_DIR)" && "$(CHECK)" dependency-lock

secret-scan:
	@cd "$(ROOT_DIR)" && sh "$(SECRET_SCAN)" $(ARGS)

rules-report:
	@cd "$(ROOT_DIR)" && sh "$(RULES_REPORT)" $(ARGS)

operator-run:
	@cd "$(ROOT_DIR)" && "$(RVV)" operator run $(ARGS)

operator-run-force:
	@cd "$(ROOT_DIR)" && "$(RVV)" operator run --force $(ARGS)

run:
	@cd "$(ROOT_DIR)" && "$(RVV)" run $(ARGS)

run-dotenvx:
	@if [ ! -x "$(DOTENVX)" ]; then echo "ERROR: dotenvx not found at $(DOTENVX). Run npm install." >&2; exit 2; fi
	@if [ ! -f "$(DOTENVX_ENV_FILE)" ]; then echo "ERROR: dotenvx env file not found: $(DOTENVX_ENV_FILE)" >&2; exit 2; fi
	@cd "$(ROOT_DIR)" && "$(DOTENVX)" run -f "$(DOTENVX_ENV_FILE)" -- "$(RVV)" run $(ARGS)

status:
	@cd "$(ROOT_DIR)" && "$(RVV)" status $(ARGS)

logs:
	@cd "$(ROOT_DIR)" && "$(RVV)" logs list $(ARGS)

calendars:
	@cd "$(ROOT_DIR)" && "$(RVV)" calendars $(ARGS)

calendars-refresh:
	@cd "$(ROOT_DIR)" && "$(RVV)" calendars --refresh $(ARGS)

calendars-refresh-dotenvx:
	@if [ ! -x "$(DOTENVX)" ]; then echo "ERROR: dotenvx not found at $(DOTENVX). Run npm install." >&2; exit 2; fi
	@if [ ! -f "$(DOTENVX_ENV_FILE)" ]; then echo "ERROR: dotenvx env file not found: $(DOTENVX_ENV_FILE)" >&2; exit 2; fi
	@cd "$(ROOT_DIR)" && "$(DOTENVX)" run -f "$(DOTENVX_ENV_FILE)" -- "$(RVV)" calendars --refresh $(ARGS)

sources-status:
	@cd "$(ROOT_DIR)" && "$(RVV)" sources status $(ARGS)

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

desktop-start:
	@cd "$(ROOT_DIR)/apps/desktop" && "$(NPM)" start $(ARGS)

desktop-clean:
	@cd "$(ROOT_DIR)/apps/desktop" && "$(NPM)" run cleanup
	@rm -rf "$(ROOT_DIR)/dist/desktop-backend" "$(ROOT_DIR)/build/desktop-backend" "$(ROOT_DIR)/apps/desktop/dist"
	@echo "✅ Cleaned bounded desktop build artifacts"

build-mac:
	@cd "$(ROOT_DIR)" && sh "$(PACKAGE_BACKEND_SH)"
	@cd "$(ROOT_DIR)/apps/desktop" && "$(NPM)" ci && "$(NPM)" run dist -- --mac dmg zip $(ARGS)
	@echo "✅ macOS build done — see apps/desktop/dist/"

build-windows:
	@cd "$(ROOT_DIR)" && "$(POWERSHELL)" -ExecutionPolicy Bypass -File "$(PACKAGE_BACKEND_PS1)"
	@cd "$(ROOT_DIR)/apps/desktop" && "$(NPM)" ci && "$(NPM)" run dist -- --win nsis $(ARGS)
	@echo "✅ Windows build done — see apps/desktop/dist/"

build-linux:
	@cd "$(ROOT_DIR)" && sh "$(PACKAGE_BACKEND_SH)"
	@cd "$(ROOT_DIR)/apps/desktop" && "$(NPM)" ci && "$(NPM)" run dist -- --linux AppImage $(ARGS)
	@echo "✅ Linux build done — see apps/desktop/dist/"

release-dry-run:
	@if [ -z "$${TAG:-}" ]; then echo "ERROR: make release-dry-run requires TAG=vX.Y.Z" >&2; exit 2; fi
	@cd "$(ROOT_DIR)" && "$(RELEASE)" --dry-run "$$TAG" $(ARGS)

release:
	@if [ -z "$${TAG:-}" ]; then echo "ERROR: make release requires TAG=vX.Y.Z" >&2; exit 2; fi
	@cd "$(ROOT_DIR)" && "$(RELEASE)" "$$TAG" $(ARGS)
