# ── HD SKYE Agentic System ────────────────────────────────────────────────────

VENV       := .venv
PY         := $(VENV)/bin/python
PIP        := $(VENV)/bin/pip
UVICORN    := $(VENV)/bin/uvicorn
HOST       := 0.0.0.0
PORT       := 8391
REDIS_PORT := 6379

# ── Development ──────────────────────────────────────────────────────────────

.PHONY: run dev redis redis-stop redis-check install venv clean help

## Start the API server
run:
	$(UVICORN) main:app --host $(HOST) --port $(PORT)

## Start the API server with hot-reload
dev:
	$(UVICORN) main:app --host $(HOST) --port $(PORT) --reload

## Start Redis + API server (all-in-one)
up: redis-start dev

## Start Redis in background
redis-start:
	@if redis-cli -p $(REDIS_PORT) ping > /dev/null 2>&1 || valkey-cli -p $(REDIS_PORT) ping > /dev/null 2>&1; then \
		echo "Redis already running on port $(REDIS_PORT)"; \
	else \
		echo "Starting Redis on port $(REDIS_PORT)..."; \
		redis-server --port $(REDIS_PORT) --daemonize yes 2>/dev/null || \
		valkey-server --port $(REDIS_PORT) --daemonize yes; \
		echo "Redis started."; \
	fi

## Stop Redis
redis-stop:
	@redis-cli -p $(REDIS_PORT) shutdown 2>/dev/null || \
	 valkey-cli -p $(REDIS_PORT) shutdown 2>/dev/null || \
	 echo "Redis not running."

## Check Redis status
redis-check:
	@redis-cli -p $(REDIS_PORT) ping 2>/dev/null || \
	 valkey-cli -p $(REDIS_PORT) ping 2>/dev/null || \
	 echo "Redis not reachable on port $(REDIS_PORT)"

# ── Setup ────────────────────────────────────────────────────────────────────

## Create virtual environment
venv:
	python3 -m venv $(VENV)

## Install dependencies
install: venv
	$(PIP) install -r requirements.txt

# ── Cleanup ──────────────────────────────────────────────────────────────────

## Remove venv
clean:
	rm -rf $(VENV)

# ── Help ─────────────────────────────────────────────────────────────────────

## Show available targets
help:
	@echo "HD SKYE Agentic System"
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@echo "  run           Start API server (port $(PORT))"
	@echo "  dev           Start API server with hot-reload"
	@echo "  up            Start Redis + API server"
	@echo "  redis-start   Start Redis in background"
	@echo "  redis-stop    Stop Redis"
	@echo "  redis-check   Check Redis status"
	@echo "  install       Create venv + install deps"
	@echo "  clean         Remove venv"
	@echo ""
