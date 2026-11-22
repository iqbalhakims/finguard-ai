#!/usr/bin/env bash
# Wazuh certificates + passwords -> two SealedSecrets in k8s/secrets/ (namespace wazuh).
#
#   scripts/wazuh_secrets.sh <kube-context>        (make doks-seal-wazuh)
#
# Plain copies live in k8s/.secrets/wazuh/ (gitignored) and are reused on every run, so
# re-sealing never changes a password or certificate unless you delete that file first.
# Only the sealed files are committed: the repo is public.
#
#   wazuh-certs        TLS between indexer, manager (Filebeat) and dashboard; one private CA
#   wazuh-credentials  indexer admin + kibanaserver passwords, the indexer's internal_users.yml
#                      (bcrypt hashes), the Wazuh API password, the agent enrollment password
set -euo pipefail

CONTEXT=${1:?usage: wazuh_secrets.sh <kube-context>}
DIR=k8s/.secrets/wazuh
OUT=k8s/secrets
NS=wazuh
# Must match plugins.security.authcz.admin_dn / nodes_dn in k8s/charts/wazuh/templates/indexer.yaml
SUBJ="/C=SG/O=FinGuard"

command -v kubeseal >/dev/null || { echo "kubeseal not found: brew install kubeseal"; exit 1; }
command -v htpasswd >/dev/null || { echo "htpasswd not found"; exit 1; }
mkdir -p "$DIR"
chmod 700 "$DIR"
umask 077

# --- passwords (created once) ------------------------------------------------------------
rand() { openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c "$1"; }
[ -f "$DIR/indexer-admin-password" ] || rand 32 > "$DIR/indexer-admin-password"
[ -f "$DIR/kibanaserver-password" ]  || rand 32 > "$DIR/kibanaserver-password"
# Wazuh API rule: 8-64 chars with upper, lower, digit and one of .*+?-
[ -f "$DIR/api-password" ]           || printf '%s.Aa1' "$(rand 28)" > "$DIR/api-password"
[ -f "$DIR/authd-password" ]         || rand 32 > "$DIR/authd-password"
[ -f "$DIR/cluster-key" ]            || rand 32 > "$DIR/cluster-key"

# --- certificates (created once) ---------------------------------------------------------
cert() {  # name, CN, optional subjectAltName
  local name=$1 cn=$2 san=${3:-}
  [ -f "$DIR/$name.pem" ] && return
  openssl genrsa -out "$DIR/$name-key-temp.pem" 2048 2>/dev/null
  openssl pkcs8 -inform PEM -outform PEM -in "$DIR/$name-key-temp.pem" -topk8 -nocrypt -out "$DIR/$name-key.pem"
  rm -f "$DIR/$name-key-temp.pem"
  openssl req -new -key "$DIR/$name-key.pem" -out "$DIR/$name.csr" -subj "$SUBJ/CN=$cn"
  if [ -n "$san" ]; then
    printf 'subjectAltName=%s\n' "$san" > "$DIR/$name.ext"
    openssl x509 -req -days 3650 -sha256 -in "$DIR/$name.csr" -CA "$DIR/root-ca.pem" -CAkey "$DIR/root-ca-key.pem" \
      -CAcreateserial -extfile "$DIR/$name.ext" -out "$DIR/$name.pem" 2>/dev/null
  else
    openssl x509 -req -days 3650 -sha256 -in "$DIR/$name.csr" -CA "$DIR/root-ca.pem" -CAkey "$DIR/root-ca-key.pem" \
      -CAcreateserial -out "$DIR/$name.pem" 2>/dev/null
  fi
  rm -f "$DIR/$name.csr" "$DIR/$name.ext"
}
if [ ! -f "$DIR/root-ca.pem" ]; then
  openssl genrsa -out "$DIR/root-ca-key.pem" 2048 2>/dev/null
  openssl req -days 3650 -new -x509 -sha256 -key "$DIR/root-ca-key.pem" -out "$DIR/root-ca.pem" -subj "$SUBJ/CN=wazuh-root-ca"
fi
cert node indexer "DNS:indexer,DNS:indexer.$NS,DNS:indexer.$NS.svc,DNS:indexer.$NS.svc.cluster.local,DNS:wazuh-indexer-0.wazuh-indexer,DNS:wazuh-indexer-0.wazuh-indexer.$NS.svc.cluster.local,DNS:localhost"
cert admin admin
cert dashboard dashboard
cert filebeat filebeat

# --- indexer users: only admin and kibanaserver (upstream's demo users are left out) -------
hash() { htpasswd -niBC 12 x < "$1" | tr -d '\n' | cut -d: -f2; }
cat > "$DIR/internal_users.yml" <<EOF
---
_meta:
  type: "internalusers"
  config_version: 2
admin:
  hash: "$(hash "$DIR/indexer-admin-password")"
  reserved: true
  backend_roles:
  - "admin"
  description: "Wazuh admin (dashboard login)"
kibanaserver:
  hash: "$(hash "$DIR/kibanaserver-password")"
  reserved: true
  description: "Wazuh dashboard server -> indexer"
EOF

# --- seal ---------------------------------------------------------------------------------
seal() {  # secret name, then kubectl --from-file args
  local name=$1; shift
  kubectl create secret generic "$name" -n "$NS" --dry-run=client -o yaml "$@" \
    | kubeseal --context "$CONTEXT" --format yaml > "$OUT/$name.yaml.tmp"
  test -s "$OUT/$name.yaml.tmp" && mv "$OUT/$name.yaml.tmp" "$OUT/$name.yaml"
  echo "wrote $OUT/$name.yaml"
}
certs=()
for f in root-ca node node-key admin admin-key dashboard dashboard-key filebeat filebeat-key; do
  certs+=(--from-file="$f.pem=$DIR/$f.pem")
done
seal wazuh-certs "${certs[@]}"
seal wazuh-credentials \
  --from-file=indexer-admin-password="$DIR/indexer-admin-password" \
  --from-file=kibanaserver-password="$DIR/kibanaserver-password" \
  --from-file=internal_users.yml="$DIR/internal_users.yml" \
  --from-file=api-password="$DIR/api-password" \
  --from-file=authd.pass="$DIR/authd-password" \
  --from-file=cluster-key="$DIR/cluster-key"
echo "Commit both files. Dashboard login: admin / $DIR/indexer-admin-password"
