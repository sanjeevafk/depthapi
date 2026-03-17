#!/usr/bin/env bash
set -euo pipefail

# Configure baseline Sentry issue + performance alerts using workflow API.
# Required env vars:
#   SENTRY_AUTH_TOKEN, SENTRY_ORG, SENTRY_USER_ID
# Optional env vars:
#   SENTRY_REGION (default: us)

if [[ -z "${SENTRY_AUTH_TOKEN:-}" || -z "${SENTRY_ORG:-}" || -z "${SENTRY_USER_ID:-}" ]]; then
  echo "Missing required env vars: SENTRY_AUTH_TOKEN, SENTRY_ORG, SENTRY_USER_ID" >&2
  exit 1
fi

SENTRY_REGION="${SENTRY_REGION:-us}"
API_BASE="https://${SENTRY_REGION}.sentry.io/api/0/organizations/${SENTRY_ORG}"
AUTH_HEADER="Authorization: Bearer ${SENTRY_AUTH_TOKEN}"

create_alert() {
  local name="$1"
  local payload="$2"

  curl -sSf --retry 3 -X POST "${API_BASE}/workflows/" \
    -H "${AUTH_HEADER}" \
    -H "Content-Type: application/json" \
    -d "${payload}" >/dev/null

  echo "Created alert: ${name}"
}

create_alert "New Issue Alert" "{
  \"name\": \"New Issue Alert\",
  \"enabled\": true,
  \"environment\": null,
  \"config\": { \"frequency\": 30 },
  \"triggers\": {
    \"logicType\": \"any-short\",
    \"conditions\": [{ \"type\": \"first_seen_event\", \"comparison\": true, \"conditionResult\": true }],
    \"actions\": []
  },
  \"actionFilters\": [{
    \"logicType\": \"all\",
    \"conditions\": [],
    \"actions\": [{
      \"type\": \"email\",
      \"integrationId\": null,
      \"data\": {},
      \"config\": { \"targetType\": \"user\", \"targetIdentifier\": \"${SENTRY_USER_ID}\", \"targetDisplay\": null },
      \"status\": \"active\"
    }]
  }]
}"

create_alert "Backend Error Spike" "{
  \"name\": \"Backend Error Spike\",
  \"enabled\": true,
  \"environment\": \"production\",
  \"config\": { \"frequency\": 10 },
  \"triggers\": {
    \"logicType\": \"any-short\",
    \"conditions\": [{ \"type\": \"regression_event\", \"comparison\": true, \"conditionResult\": true }],
    \"actions\": []
  },
  \"actionFilters\": [{
    \"logicType\": \"all\",
    \"conditions\": [{ \"type\": \"event_frequency_count\", \"comparison\": { \"value\": 20, \"interval\": \"1hr\" }, \"conditionResult\": true }],
    \"actions\": [{
      \"type\": \"email\",
      \"integrationId\": null,
      \"data\": {},
      \"config\": { \"targetType\": \"user\", \"targetIdentifier\": \"${SENTRY_USER_ID}\", \"targetDisplay\": null },
      \"status\": \"active\"
    }]
  }]
}"

create_alert "Slow Transaction Alert" "{
  \"name\": \"Slow Transaction Alert\",
  \"enabled\": true,
  \"environment\": \"production\",
  \"config\": { \"frequency\": 10 },
  \"triggers\": {
    \"logicType\": \"any-short\",
    \"conditions\": [{ \"type\": \"first_seen_event\", \"comparison\": true, \"conditionResult\": true }],
    \"actions\": []
  },
  \"actionFilters\": [{
    \"logicType\": \"all\",
    \"conditions\": [{ \"type\": \"event_frequency_count\", \"comparison\": { \"value\": 10, \"interval\": \"15min\" }, \"conditionResult\": true }],
    \"actions\": [{
      \"type\": \"email\",
      \"integrationId\": null,
      \"data\": {},
      \"config\": { \"targetType\": \"user\", \"targetIdentifier\": \"${SENTRY_USER_ID}\", \"targetDisplay\": null },
      \"status\": \"active\"
    }]
  }]
}"

echo "Sentry alert bootstrap completed"
