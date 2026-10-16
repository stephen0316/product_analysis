#!/usr/bin/env bash
# Run on the Linux deployment host. No application secrets are stored here.
set -Eeuo pipefail
umask 077

image=${1:?Usage: bash scripts/deploy.sh IMAGE_REFERENCE}
deploy_dir=${PRODUCT_EVAL_DEPLOY_DIR:-/opt/product-evaluation}
source_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [[ ! "$image" =~ ^ghcr\.io/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$ ]]; then
  echo "Expected a GHCR image digest: ghcr.io/owner/repo@sha256:<64 hex characters>" >&2
  exit 1
fi
if [[ ! -d "$deploy_dir" || ! -f "$deploy_dir/.env" ]]; then
  echo "First configure $deploy_dir/.env following docs/deployment.md." >&2
  exit 1
fi
exec 9>"$deploy_dir/deploy.lock"
flock -n 9 || { echo "Another deployment is running." >&2; exit 1; }
cd "$deploy_dir"
mkdir -p backups

# The pull completes before interrupting the running application.
docker pull "$image"
previous_image=""
if [[ -f image.env ]]; then
  previous_image=$(sed -n 's/^PRODUCT_EVAL_IMAGE=//p' image.env)
fi
if [[ -n "$previous_image" && ! -f docker-compose.yml ]]; then
  echo "Missing existing Compose file; refusing to change the deployment." >&2
  exit 1
fi
had_compose=false
if [[ -f docker-compose.yml ]]; then
  cp docker-compose.yml compose.previous.yml
  had_compose=true
fi
if [[ ! -f image.env ]]; then
  printf 'PRODUCT_EVAL_IMAGE=%s\n' "$image" > image.env
fi
compose() {
  docker compose -p product-evaluation --env-file .env --env-file image.env -f docker-compose.yml "$@"
}
wait_healthy() {
  local container status attempt
  for attempt in $(seq 1 60); do
    container=$(compose ps -a -q app)
    status=""
    if [[ -n "$container" ]]; then
      status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)
    fi
    if [[ "$status" == healthy ]]; then return 0; fi
    if [[ "$status" == exited || "$status" == dead || "$status" == unhealthy ]]; then return 1; fi
    sleep 2
  done
  return 1
}

changing=false
backup_tmp=""
recover() {
  local result=$?
  trap - EXIT
  if [[ "$result" -ne 0 && "$changing" == true ]]; then
    echo "Deployment failed; attempting to restart the previous application version." >&2
    if [[ -n "$previous_image" ]]; then
      printf 'PRODUCT_EVAL_IMAGE=%s\n' "$previous_image" > image.env
      if [[ "$had_compose" == true ]]; then cp compose.previous.yml docker-compose.yml; fi
      if compose up -d --no-build --pull never app && wait_healthy; then
        echo "Previous application version is healthy. Deployment remains failed." >&2
      else
        echo "Recovery failed. Operator intervention is required; preserve backups and logs." >&2
      fi
    else
      echo "No previous managed image exists; inspect the failed first deployment." >&2
    fi
    echo "The database was not automatically restored. Review schema compatibility before restoring a backup." >&2
  fi
  if [[ -n "$backup_tmp" ]]; then rm -f "$backup_tmp"; fi
  exit "$result"
}
trap recover EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ "$had_compose" == true ]]; then
  existing_id=$(compose ps -a -q app)
  if [[ -n "$existing_id" ]]; then
    # Keep the old container so its mounts can be backed up consistently.
    if [[ -z "$previous_image" ]]; then
      previous_image=$(docker inspect --format '{{.Config.Image}}' "$existing_id")
    fi
    changing=true
    compose stop app
    backup_tmp="backups/$(date -u +%Y%m%dT%H%M%SZ)-pre-deploy.tar.gz.tmp"
    docker run --rm --user 0:0 --volumes-from "$existing_id:ro" --entrypoint python "$previous_image" -c \
      'import sys,tarfile; archive=tarfile.open(fileobj=sys.stdout.buffer,mode="w|gz"); archive.add("/app/local_data",arcname="local_data"); archive.close()' > "$backup_tmp"
    mv "$backup_tmp" "${backup_tmp%.tmp}"
    backup_tmp=""
  elif [[ -n "$previous_image" ]]; then
    # Compose down may remove containers while leaving the data volume intact.
    changing=true
    backup_tmp="backups/$(date -u +%Y%m%dT%H%M%SZ)-pre-deploy.tar.gz.tmp"
    compose run --rm --no-deps -T --user 0:0 --entrypoint python app -c \
      'import sys,tarfile; archive=tarfile.open(fileobj=sys.stdout.buffer,mode="w|gz"); archive.add("/app/local_data",arcname="local_data"); archive.close()' > "$backup_tmp"
    mv "$backup_tmp" "${backup_tmp%.tmp}"
    backup_tmp=""
  fi
fi

changing=true
cp "$source_dir/docker-compose.yml" docker-compose.yml
printf 'PRODUCT_EVAL_IMAGE=%s\n' "$image" > image.env
compose up -d --no-build --pull never app
if ! wait_healthy; then
  echo "New container did not become healthy." >&2
  compose logs --tail 80 app >&2 || true
  exit 1
fi
if [[ -n "$previous_image" ]]; then
  printf 'PRODUCT_EVAL_IMAGE=%s\n' "$previous_image" > previous-image.env
fi
printf '%s\n' "$image" > deployed-image.txt
echo "Deployment healthy: $image"
