#!/usr/bin/env bash
# Deploy only to the explicitly authorized Binance/GMGN VPS.
set -euo pipefail

HOST="root@173.199.122.23"
KEY="$HOME/.ssh/polymtrade_vultr_ed25519"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_JSON=""
ENABLE_DAILY=0

usage() {
  echo "Usage: bash deploy/deploy-vps.sh [--run-json /path/to/sanitized-run.json] [--enable-daily]"
  echo "Target is fixed: root@173.199.122.23; daily timer is not enabled by default."
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --run-json)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      RUN_JSON="$2"
      shift 2
      ;;
    --enable-daily) ENABLE_DAILY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

[ "$HOST" = "root@173.199.122.23" ] || { echo "ABORT: unauthorized deployment target"; exit 1; }
[ -r "$KEY" ] || { echo "ABORT: SSH identity is unavailable: $KEY"; exit 1; }
[ -f "$ROOT/multiedge/jobs.py" ] || { echo "ABORT: research jobs module is missing"; exit 1; }
[ -f "$ROOT/requirements.txt" ] || { echo "ABORT: requirements.txt is missing"; exit 1; }
if [ -n "$RUN_JSON" ]; then
  [[ "$RUN_JSON" = /* ]] || { echo "ABORT: --run-json requires an absolute path"; exit 1; }
  [ -f "$RUN_JSON" ] || { echo "ABORT: run JSON is missing"; exit 1; }
  python3 -c 'import json,sys; json.load(open(sys.argv[1], encoding="utf-8"))' "$RUN_JSON"
fi

SSH=(ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=15 -o HostName=173.199.122.23 "$HOST")
SCP=(scp -i "$KEY" -o BatchMode=yes -o ConnectTimeout=15 -o HostName=173.199.122.23)
REV="$(git -C "$ROOT" rev-parse --short HEAD)"
RELEASE="$(date -u +%Y%m%dT%H%M%SZ)-$REV"
STAGE="/opt/multiedge/.stage-$RELEASE"
LOCAL_STAGE="$(mktemp -d "${TMPDIR:-/tmp}/multiedge-deploy.XXXXXX")"
trap 'rm -rf -- "$LOCAL_STAGE"' EXIT

echo "Checking authorized VPS, existing login and Caddy configuration"
"${SSH[@]}" 'set -eu
command -v python3 >/dev/null
command -v caddy >/dev/null
test -f /etc/caddy/Caddyfile
systemctl is-active --quiet caddy
test "$(curl -sS --max-time 10 -o /dev/null -w "%{http_code}" http://127.0.0.1:4173/health)" = 401
if ss -ltnH "sport = :8321" | grep -q .; then
  systemctl is-active --quiet multiedge || { echo "ABORT: port 8321 belongs to another service"; exit 1; }
fi'

echo "Packaging source without credentials, local context or runtime state"
tar -czf "$LOCAL_STAGE/source.tar.gz" \
  --exclude='./.git' --exclude='./.venv' --exclude='./.multica' --exclude='./.kimi' \
  --exclude='./.multica-import' --exclude='./.codex' --exclude='./state' --exclude='./runtime' \
  --exclude='./output' --exclude='./.playwright-cli' \
  --exclude='./docs/讨论上下文.md' --exclude='__pycache__' --exclude='.pytest_cache' \
  --exclude='.DS_Store' --exclude='*.pyc' --exclude='.env*' --exclude='*.pem' \
  --exclude='*.key' --exclude='credentials.json' --exclude='secrets.json' --exclude='*.sqlite*' \
  -C "$ROOT" .
"${SSH[@]}" "install -d -m 0700 '$STAGE'"
"${SCP[@]}" "$LOCAL_STAGE/source.tar.gz" "$HOST:$STAGE/source.tar.gz"
if [ -n "$RUN_JSON" ]; then
  "${SCP[@]}" "$RUN_JSON" "$HOST:$STAGE/initial-run.json"
fi

echo "Installing isolated release $RELEASE"
"${SSH[@]}" bash -s -- "$RELEASE" "$ENABLE_DAILY" <<'REMOTE'
set -Eeuo pipefail
release_name="$1"
enable_daily="$2"
[[ "$release_name" =~ ^[0-9]{8}T[0-9]{6}Z-[a-f0-9]+$ ]] || exit 2
stage="/opt/multiedge/.stage-$release_name"
release="/opt/multiedge/releases/$release_name"
state=/var/lib/multiedge
cf=/etc/caddy/Caddyfile
previous="$(readlink -f /opt/multiedge/current 2>/dev/null || true)"
activated=0
caddy_changed=0
was_active=0
systemctl is-active --quiet multiedge && was_active=1
was_enabled=0
systemctl is-enabled --quiet multiedge.service && was_enabled=1
timer_was_enabled=0
systemctl is-enabled --quiet multiedge-daily.timer && timer_was_enabled=1
timer_was_active=0
systemctl is-active --quiet multiedge-daily.timer && timer_was_active=1

rollback() {
  status=$?
  trap - EXIT
  set +e
  if [ "$caddy_changed" = 1 ]; then
    cp -p "$release/Caddyfile.before" "$cf"
    caddy validate --config "$cf" --adapter caddyfile && systemctl reload caddy
  fi
  if [ "$activated" = 1 ]; then
    if [ "$timer_was_active" = 0 ]; then systemctl stop multiedge-daily.timer; fi
    if [ "$timer_was_enabled" = 0 ]; then systemctl disable multiedge-daily.timer; fi
    if [ "$was_enabled" = 0 ]; then systemctl disable multiedge.service; fi
    if [ -n "$previous" ]; then
      ln -sfn "$previous" /opt/multiedge/.current-rollback
      mv -Tf /opt/multiedge/.current-rollback /opt/multiedge/current
    else
      rm -f /opt/multiedge/current
    fi
    for unit in multiedge.service multiedge-daily.service multiedge-daily.timer; do
      if [ -f "$release/.previous-units/$unit" ]; then
        cp -p "$release/.previous-units/$unit" "/etc/systemd/system/$unit"
      else
        rm -f "/etc/systemd/system/$unit"
      fi
    done
    systemctl daemon-reload
    if [ "$was_active" = 1 ]; then systemctl restart multiedge; else systemctl stop multiedge; fi
    if [ "$timer_was_active" = 1 ]; then systemctl restart multiedge-daily.timer; fi
  fi
  echo "Deployment failed; previous service and Caddy configuration restored where present. State and releases retained."
  exit "$status"
}
trap rollback EXIT

getent passwd multiedge >/dev/null || useradd --system --user-group --home-dir "$state" --shell /usr/sbin/nologin multiedge
install -d -m 0755 /opt/multiedge/releases "$release"
install -d -o multiedge -g multiedge -m 0750 "$state" "$state/data" "$state/imports"
tar -xzf "$stage/source.tar.gz" -C "$release"
python3 -m venv "$release/.venv"
"$release/.venv/bin/pip" install --disable-pip-version-check --no-cache-dir -r "$release/requirements.txt"
chown -R root:root "$release"
cd "$release"
runuser -u multiedge -- env PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  "$release/.venv/bin/python" -m pytest tests/ -q -p no:cacheprovider
for csv in "$release"/data/*.csv; do
  if [ -f "$csv" ] && [ ! -e "$state/data/$(basename "$csv")" ]; then
    install -o multiedge -g multiedge -m 0600 "$csv" "$state/data/"
  fi
done
runuser -u multiedge -- env PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  "$release/.venv/bin/python" -m multiedge.jobs seed --state-dir "$state" --data-dir "$state/data"
if [ -f "$stage/initial-run.json" ]; then
  import_path="$state/imports/$release_name.json"
  install -o multiedge -g multiedge -m 0600 "$stage/initial-run.json" "$import_path"
  runuser -u multiedge -- env PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
    "$release/.venv/bin/python" -m multiedge.jobs import-run --path "$import_path" --state-dir "$state" --data-dir "$state/data"
fi

install -d -m 0700 "$release/.previous-units"
for unit in multiedge.service multiedge-daily.service multiedge-daily.timer; do
  if [ -f "/etc/systemd/system/$unit" ]; then cp -p "/etc/systemd/system/$unit" "$release/.previous-units/$unit"; fi
done
cp -p "$cf" "$release/Caddyfile.before"
activated=1
for unit in multiedge.service multiedge-daily.service multiedge-daily.timer; do
  install -m 0644 "$release/deploy/$unit" "/etc/systemd/system/$unit"
done
ln -sfn "$release" /opt/multiedge/.current-next
mv -Tf /opt/multiedge/.current-next /opt/multiedge/current
systemctl daemon-reload
systemctl enable multiedge.service
systemctl restart multiedge.service
for attempt in {1..15}; do
  if curl --fail --silent --show-error --max-time 3 http://127.0.0.1:8321/health > "$stage/health.json"; then break; fi
  sleep 1
done
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8321/health

caddy_changed=1
python3 - "$cf" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
start, end = "\t# >>> multiedge module >>>", "\t# <<< multiedge module <<<"
block = r'''\t# >>> multiedge module >>>
\tredir /multiedge /multiedge/ 308
\thandle_path /multiedge/* {
\t\t@multiedge_cross_site_write {
\t\t\tmethod POST PUT PATCH DELETE
\t\t\tnot header_regexp Origin ^https://(www\.)?spaceflag\.site$
\t\t}
\t\trespond @multiedge_cross_site_write "forbidden" 403
\t\tforward_auth 127.0.0.1:4173 {
\t\t\turi /health
\t\t\t@unauthorized status 401 403
\t\t\thandle_response @unauthorized {
\t\t\t\tredir * /login 303
\t\t\t}
\t\t}
\t\treverse_proxy 127.0.0.1:8321 {
\t\t\theader_up X-Forwarded-Prefix /multiedge
\t\t}
\t}
\t# <<< multiedge module <<<'''.replace(r"\t", "\t")
site = re.search(r"^spaceflag\.site[^\n]*\{\n(.*?)^\}", text, re.S | re.M)
if not site:
    sys.exit("ABORT: expected spaceflag.site block was not found")
body = site.group(1)
if start in body:
    if body.count(start) != 1 or body.count(end) != 1:
        sys.exit("ABORT: ambiguous existing Multi-Edge route")
    new_body = re.sub(re.escape(start) + r".*?" + re.escape(end), lambda _: block, body, flags=re.S)
else:
    marker = "\t# >>> gmgn module >>>"
    if body.count(marker) != 1 or "forward_auth 127.0.0.1:4173" not in body:
        sys.exit("ABORT: expected GMGN/Binance shared-login route was not found")
    new_body = body.replace(marker, block + "\n" + marker, 1)
path.write_text(text[:site.start(1)] + new_body + text[site.end(1):], encoding="utf-8")
PY
caddy validate --config "$cf" --adapter caddyfile
systemctl reload caddy
code="$(curl -sS --max-time 15 --resolve spaceflag.site:443:127.0.0.1 -o /dev/null -w '%{http_code}' https://spaceflag.site/multiedge/)"
[ "$code" = 303 ] || { echo "ABORT: unauthenticated public route returned $code, expected 303"; exit 1; }
test "$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' http://127.0.0.1:4173/health)" = 401
systemctl is-active --quiet multiedge.service
if [ "$enable_daily" = 1 ]; then systemctl enable --now multiedge-daily.timer; fi
trap - EXIT
rm -rf -- "$stage"
echo "Release installed: $release"
echo "Previous release: ${previous:-none}; all releases and persistent state retained"
systemctl --no-pager --lines=5 status multiedge.service
systemctl is-enabled multiedge-daily.timer || true
REMOTE

echo "Dashboard: https://spaceflag.site/multiedge/ (existing Binance dashboard login)"
