FROM python:3.11-slim

WORKDIR /app

COPY . /tmp/src
RUN set -eux; \
		if [ -f /tmp/src/api/requirements.txt ]; then \
			cp /tmp/src/api/requirements.txt /app/requirements.txt; \
			pip install --no-cache-dir -r /app/requirements.txt; \
			cp -a /tmp/src/api/. /app/; \
		elif [ -f /tmp/src/requirements.txt ]; then \
			cp /tmp/src/requirements.txt /app/requirements.txt; \
			pip install --no-cache-dir -r /app/requirements.txt; \
			cp -a /tmp/src/. /app/; \
		else \
			echo "No requirements.txt found in expected paths"; \
			exit 1; \
		fi; \
		if [ -f /tmp/src/shared/chat_modes.json ]; then \
			mkdir -p /shared; \
			cp /tmp/src/shared/chat_modes.json /shared/chat_modes.json; \
		fi; \
		rm -rf /tmp/src

EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
