# Single entry point for humans, agents and CI.

RIG      ?= A
HOURS    ?= 72
SOAK_CMD ?=
SOAK_ARGS ?=

.PHONY: build test hil soak scrub-check prepublish audit setup

build:
	test -x ntsc-tvh
	python3 -m py_compile ntsc-tvh

test:
	python3 tools/scrub.py --self-test
	PYTHONDONTWRITEBYTECODE=1 python3 -W error::ResourceWarning -m unittest discover -s tests

hil:
	./ntsc-tvh doctor

soak:
	@test -n "$(SOAK_CMD)" || { echo 'usage: make soak RIG=A HOURS=72 SOAK_CMD="..." [SOAK_ARGS="--error-pattern ..."]'; exit 2; }
	python3 tools/soak.py --rig $(RIG) --hours $(HOURS) --append $(SOAK_ARGS) --cmd "$(SOAK_CMD)"

scrub-check:
	@git grep -Il '' -- . ':!tools/scrub.py' ':!*lock.json' ':!*.lock' | xargs -r python3 tools/scrub.py --check

prepublish:
	bash tools/prepublish.sh

# Audit ALL your GitHub repos (full history). Read-only. Report goes to ~/.cache/repo-audit/
audit:
	python3 tools/audit-all.py $(AUDIT_ARGS)

setup:
	@command -v gitleaks >/dev/null || { echo "gitleaks not found. Install it first: https://github.com/gitleaks/gitleaks/releases"; exit 1; }
	@command -v pre-commit >/dev/null || pipx install pre-commit || python3 -m pip install --user pre-commit
	pre-commit install --allow-missing-config   # hooks are shared: other branches may not have the config yet
