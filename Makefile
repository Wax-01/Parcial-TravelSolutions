.PHONY: up down reset test e2e audit lock

up:            ## Levanta todo el ecosistema
	docker compose up --build -d

down:
	docker compose down

reset:         ## Baja y borra volúmenes (Prefect DB); el catálogo vive en Supabase
	docker compose down -v

test:          ## Pruebas unitarias (requiere: pip install -r requirements-dev.txt)
	cd services/orders && PYTHONPATH=. python -m pytest tests -q
	cd services/gateway && PYTHONPATH=. python -m pytest tests -q
	PYTHONPATH=. python -m pytest ingestion/tests -q

e2e:           ## Verificación end-to-end contra el stack levantado (:8080)
	python scripts/e2e.py

audit:         ## pip-audit + npm audit -> docs/seguridad/
	bash scripts/audit.sh

lock:          ## Regenera requirements.txt con hashes (dentro de Python 3.12)
	docker run --rm -v "$$(pwd):/w" -w /w python:3.12-slim sh -c 'pip install -q pip-tools && for d in services/gateway services/orders services/flights services/hotels services/cars db ingestion; do pip-compile -q --generate-hashes --strip-extras --no-header --allow-unsafe -o $$d/requirements.txt $$d/requirements.in; done'
