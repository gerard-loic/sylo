SHELL := /bin/bash

up: ## Lance l'environement de production
	@git config core.fileMode false ;\
	docker compose --env-file docker/.env -f docker/docker-compose.yml build; \
	docker compose --env-file docker/.env -f docker/docker-compose.yml up -d; \

down: ## Arrête les conteneurs de prod et supprime les conteneurs, les réseaux, les volumes et les images
	@docker compose --env-file docker/.env -f docker/docker-compose.yml down


help: ## Affiche la liste des commandes disponibles
	@IFS=$$'\n' ; \
	help_lines=(`fgrep -h "##" $(MAKEFILE_LIST) | fgrep -v fgrep | sed -e 's/\\$$//' | sed -e 's/##/:/'`); \
	printf "%-30s %s\n" "target" "help" ; \
	printf "%-30s %s\n" "------" "----" ; \
	for help_line in $${help_lines[@]}; do \
	IFS=$$':' ; \
	help_split=($$help_line) ; \
	help_command=`echo $${help_split[0]} | sed -e 's/^ *//' -e 's/ *$$//'` ; \
	help_info=`echo $${help_split[2]} | sed -e 's/^ *//' -e 's/ *$$//'` ; \
	printf '\033[36m'; \
	printf "%-30s %s" $$help_command ; \
	printf '\033[0m'; \
	printf "%s\n" $$help_info; \
	done