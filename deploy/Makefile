# Azure, in bootstrap order (SPEC §9, deploy/README.md). Run from deploy/, signed in with
# `az login`, with terraform (or TF=tofu) and docker.
#
#   make infra            the base: group, logs, environment, registry, identity, share, alerts
#   make storage-link     the share's key, from one az command straight into the next
#   make image            build for linux/amd64, push, record the digest
#   make deploy           the gate must hold for this agent, then the app and the job
#   make secrets          the model key from python/.env; a salt and a session key, once
#   make user NAME=ana    a sign-in account; its password goes to deploy/.passwords/ana.txt
#   make url | logs | gate-now | alert-test | calibrate | restart | rotate-session | destroy
#
# Terraform's state holds the storage account's keys and, once refreshed, the app's
# secrets: it is local, git-ignored and written owner-only (umask 077). Treat it as a
# secret; a remote, encrypted backend is the next step (SPEC §12).

SHELL := /bin/bash
TF ?= terraform
APPROVE ?= # -auto-approve to skip Terraform's own prompt
TFDIR := terraform
TFRUN = umask 077 && $(TF) -chdir=$(TFDIR)
ENVFILE := ../python/.env

export ARM_SUBSCRIPTION_ID ?= $(shell az account show --query id -o tsv 2>/dev/null)

out = $(shell $(TF) -chdir=$(TFDIR) output -raw $(1) 2>/dev/null)
RG = $(call out,resource_group)
ACR = $(call out,registry)
APP = $(call out,app)
JOB = $(call out,job)
DIGEST = $(shell cat .image-digest 2>/dev/null)
# the commit the image is built from; -dirty when uncommitted changes are in it
TAG := $(shell git describe --always --dirty --abbrev=7)

.PHONY: init infra storage-link image gate deploy secrets user user-remove users rotate-session restart url logs gate-now alert-test calibrate destroy

init:
	$(TFRUN) init -input=false

infra: init
	@test -f $(TFDIR)/terraform.tfvars || { echo "copy terraform/terraform.tfvars.example to terraform/terraform.tfvars and set alert_email"; exit 2; }
	$(TFRUN) apply -input=false $(APPROVE) -var image_digest=

# Container Apps still mounts SMB with the account key (managed-identity mounts are in
# preview): it goes from `keys list` straight into `env storage set`, never into a file.
storage-link:
	az containerapp env storage set -o none \
	  -g $(RG) -n $(call out,environment) --storage-name $(call out,storage_link) \
	  --access-mode ReadWrite --azure-file-share-name $(call out,share) \
	  --azure-file-account-name $(call out,storage_account) \
	  --azure-file-account-key "$$(az storage account keys list -g $(RG) -n $(call out,storage_account) --query '[0].value' -o tsv)"
	@echo "linked the share to the environment as $(call out,storage_link)"

# Built here and pushed with an Entra token (no registry password); Azure runs amd64.
image:
	az acr login -n $(ACR)
	docker buildx build --platform linux/amd64 -f Dockerfile -t $(ACR).azurecr.io/pirtis:$(TAG) --push ..
	az acr repository show -n $(ACR) --image pirtis:$(TAG) --query digest -o tsv > .image-digest
	@echo "pirtis:$(TAG) is $$(cat .image-digest)"

# D-21: a failing gate blocks a deploy. A saved full pass for this agent version counts;
# otherwise the full gate runs now.
gate:
	cd ../python && { uv run python -m pirtis.ops.gate_check || $(MAKE) gate; }

deploy: gate
	@test -n "$(DIGEST)" || { echo "no image yet: make image"; exit 2; }
	$(TFRUN) apply -input=false $(APPROVE) -var image_digest=$(DIGEST)
	@echo "deployed $(DIGEST) to $$($(TF) -chdir=$(TFDIR) output -raw url)"

# The model key is read from python/.env and never printed. The salt and the session key
# are made once: a new salt would unlink old phone hashes, a new key signs everyone out.
secrets:
	@set -euo pipefail; \
	key=$$(grep -E '^LLM_API_KEY=' $(ENVFILE) | cut -d= -f2- | tr -d "\"'"); \
	test -n "$$key" || { echo "no LLM_API_KEY in $(ENVFILE)"; exit 2; }; \
	salt=$$(az containerapp secret show -g $(RG) -n $(APP) --secret-name hash-salt --query value -o tsv); \
	[ "$$salt" != unset ] || salt=$$(openssl rand -hex 32); \
	session=$$(az containerapp secret show -g $(RG) -n $(APP) --secret-name session-key --query value -o tsv); \
	[ "$$session" != unset ] || session=$$(openssl rand -hex 32); \
	az containerapp secret set -o none -g $(RG) -n $(APP) --secrets llm-api-key="$$key" hash-salt="$$salt" session-key="$$session"; \
	az containerapp job secret set -o none -g $(RG) -n $(JOB) --secrets llm-api-key="$$key" hash-salt="$$salt"; \
	echo "secrets set on $(APP) and $(JOB)"
	$(MAKE) restart

user:
	@test -n "$(NAME)" || { echo "usage: make user NAME=ana"; exit 2; }
	cd ../python && uv run python -m pirtis.playground.auth add $(NAME)
	$(MAKE) users

user-remove:
	@test -n "$(NAME)" || { echo "usage: make user-remove NAME=ana"; exit 2; }
	cd ../python && uv run python -m pirtis.playground.auth remove $(NAME)
	$(MAKE) users

# The accounts file holds names and scrypt hashes only; it becomes one secret. With no
# accounts left the secret goes back to "unset", and the app refuses to start (fails closed).
users:
	@accounts=$$(grep -v '^#' .users 2>/dev/null | grep . | paste -sd, -); \
	if [ -z "$$accounts" ]; then accounts=unset; echo "no accounts left: the app refuses to start until one is added"; fi; \
	az containerapp secret set -o none -g $(RG) -n $(APP) --secrets users="$$accounts"; \
	echo "$$(grep -c . .users 2>/dev/null || echo 0) account(s) set on $(APP)"
	$(MAKE) restart

# A new session key signs everyone out at once.
rotate-session:
	@az containerapp secret set -o none -g $(RG) -n $(APP) --secrets session-key="$$(openssl rand -hex 32)"
	@echo "new session key on $(APP): everyone is signed out"
	$(MAKE) restart

# A secret changes nothing until the revision restarts.
restart:
	az containerapp revision restart -o none -g $(RG) -n $(APP) \
	  --revision "$$(az containerapp show -g $(RG) -n $(APP) --query properties.latestRevisionName -o tsv)"
	@echo "restarted $(APP)"

url:
	@$(TF) -chdir=$(TFDIR) output -raw url; echo

logs:
	az containerapp logs show -g $(RG) -n $(APP) --type console --follow --format text

gate-now:
	az containerapp job start -o none -g $(RG) -n $(JOB)
	@echo "started $(JOB); its one line arrives in the logs, its run in the Reliability view"

# Fires the gate-regression alert end to end: the nightly suite with a key that can't
# work, so every call fails at once (no tokens), the line says passed=false with
# llm_errors, and the job exits 1. Its records go to /tmp, not the share's history.
alert-test:
	az containerapp job start -o none -g $(RG) -n $(JOB) \
	  --image $(ACR).azurecr.io/pirtis@$(DIGEST) --command gate-job \
	  --env-vars LLM_API_KEY=not-a-key-on-purpose PIRTIS_HASH_SALT=alert-test PIRTIS_DATA_DIR=/tmp/alert-test
	@echo "started an alert test; the email follows within about 20 minutes"

# Judge calibration on the ratings testers saved in the cloud (SPEC §9b): one run of the
# job's image with the calibrate command, the real key by secret reference, the share at
# /data. Its result is in the logs and the admin console's Human eval view.
calibrate:
	az containerapp job start -o none -g $(RG) -n $(JOB) \
	  --image $(ACR).azurecr.io/pirtis@$(DIGEST) --command calibrate \
	  --env-vars LLM_API_KEY=secretref:llm-api-key PIRTIS_HASH_SALT=secretref:hash-salt
	@echo "started calibration; read it with make logs or in the Human eval view"

destroy:
	@read -p "Delete every resource in $(RG)? Type the group's name: " name && [ "$$name" = "$(RG)" ]
	$(TFRUN) destroy -input=false -var image_digest=$(DIGEST)
