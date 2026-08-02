.PHONY: up down reset shell logs dev
up:
	docker compose up -d
down:
	docker compose down
reset:
	docker compose down -v
	docker compose up -d
shell:
	docker compose exec postgres psql -U depthapi -d depthapi
logs:
	docker compose logs -f
dev:
	uvicorn api.main:app --reload
