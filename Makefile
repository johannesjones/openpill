.PHONY: help setup mongo-up mongo-down mongo-logs api proxy server test test-unit test-integration smoke openclaw-smoke ci-local tool-search tool-semantic tool-get tool-neighbors tool-categories tool-topics md-ingest md-watch md-watch-install md-watch-uninstall

help:
	@echo "OpenPill developer commands"
	@echo ""
	@echo "  make setup             Install Python dependencies"
	@echo "  make mongo-up          Start MongoDB via docker compose"
	@echo "  make mongo-down        Stop MongoDB containers"
	@echo "  make mongo-logs        Tail MongoDB logs"
	@echo "  make api               Run REST API on :8080"
	@echo "  make proxy             Run OpenPill proxy on :4000"
	@echo "  make server            Run MCP server (stdio)"
	@echo "  make test              Run full test suite"
	@echo "  make test-unit         Run unit tests (no Mongo integration)"
	@echo "  make test-integration  Run Mongo integration tests"
	@echo "  make smoke             Run local Mongo smoke script"
	@echo "  make openclaw-smoke    Run OpenClaw guardrail smoke script"
	@echo "  make ci-local          Run local CI-equivalent checks"
	@echo ""
	@echo "OpenPill tool shortcuts:"
	@echo "  make tool-search Q='...'"
	@echo "  make tool-semantic Q='...' [LIMIT=10] [HYBRID=false]"
	@echo "  make tool-get ID='<pill_id>'"
	@echo "  make tool-neighbors ID='<pill_id>'"
	@echo "  make tool-categories"
	@echo "  make tool-topics [TOP=20] [PER=10] [MIN_DF=2] [MIN_LEN=3]"
	@echo "  make md-ingest [ROOT=. ] [STATE=.openpill_md_ingest_state.json]"
	@echo "  make md-watch [ROOT=. ] [STATE=.openpill_md_ingest_state.json] [INTERVAL=60]"
	@echo "  make md-watch-install [ROOT=. ] [INTERVAL=60]"
	@echo "  make md-watch-uninstall"

setup:
	pip install -r requirements.txt

mongo-up:
	docker compose up -d

mongo-down:
	docker compose down

mongo-logs:
	docker compose logs -f

api:
	python api.py

proxy:
	python proxy.py

server:
	python server.py

test:
	pytest tests/ -v

test-unit:
	pytest tests/ -v --ignore=tests/test_mongo_integration.py

test-integration:
	RUN_MONGO_INTEGRATION=1 pytest tests/test_mongo_integration.py -v

smoke:
	bash scripts/integration_mongo_smoke.sh

openclaw-smoke:
	bash scripts/openclaw_guardrail_smoke.sh

ci-local: test-unit test-integration

tool-search:
	@test -n "$(Q)" || (echo "Usage: make tool-search Q='query' [LIMIT=20]" && exit 1)
	bash scripts/openpill_tools.sh search_pills "$(Q)" "$(or $(LIMIT),20)"

tool-semantic:
	@test -n "$(Q)" || (echo "Usage: make tool-semantic Q='query' [LIMIT=10] [HYBRID=false]" && exit 1)
	bash scripts/openpill_tools.sh semantic_search "$(Q)" "$(or $(LIMIT),10)" "$(or $(HYBRID),false)"

tool-get:
	@test -n "$(ID)" || (echo "Usage: make tool-get ID='<pill_id>'" && exit 1)
	bash scripts/openpill_tools.sh get_pill "$(ID)"

tool-neighbors:
	@test -n "$(ID)" || (echo "Usage: make tool-neighbors ID='<pill_id>'" && exit 1)
	bash scripts/openpill_tools.sh get_pill_neighbors "$(ID)"

tool-categories:
	bash scripts/openpill_tools.sh list_categories

tool-topics:
	bash scripts/openpill_tools.sh topics_snapshot "$(or $(TOP),20)" "$(or $(PER),10)" "$(or $(MIN_DF),2)" "$(or $(MIN_LEN),3)"

md-ingest:
	python scripts/ingest_markdown_memory.py --root "$(or $(ROOT),.)" --state-file "$(or $(STATE),.openpill_md_ingest_state.json)"

md-watch:
	python scripts/ingest_markdown_memory.py --root "$(or $(ROOT),.)" --state-file "$(or $(STATE),.openpill_md_ingest_state.json)" --interval "$(or $(INTERVAL),60)"

md-watch-install:
	OPENPILL_MD_WATCH_ROOT="$(or $(ROOT),.)" OPENPILL_MD_WATCH_INTERVAL="$(or $(INTERVAL),60)" python scripts/install_md_watch_autostart.py

md-watch-uninstall:
	python scripts/uninstall_md_watch_autostart.py
