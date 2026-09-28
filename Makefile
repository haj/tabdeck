.PHONY: install-deps setup install uninstall test test-all logs restart widget deploy-hub agent server move
# TABDECK_INSTANCE=<name> selects a second setup (~/.tabdeck-<name>, com.tabdeck-<name>.*).
SUFFIX := $(if $(TABDECK_INSTANCE),-$(TABDECK_INSTANCE),)
install:
	uv sync && uv run tabdeck install
uninstall:
	uv run tabdeck uninstall
test:
	uv run pytest
test-all:
	uv run pytest -m "" -v
logs:
	tail -f ~/.tabdeck$(SUFFIX)/service.log
restart:
	launchctl kickstart -k gui/$$(id -u)/com.tabdeck$(SUFFIX).service
widget:
	uv run tabdeck install-widget
deploy-hub:
	sh scripts/deploy-hub.sh
agent:
	uv run tabdeck install-agent
server:
	uv run tabdeck install-server $(HOST) $(NAME) --projects $(or $(PROJECTS),Projects)
move:
	uv run tabdeck move $(PROJECT) $(SERVER) $(if $(SESSION),--session $(SESSION))
install-deps:
	uv sync
setup:
	uv run tabdeck setup
