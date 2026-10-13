#!/bin/sh
# Выпускает сертификаты Let's Encrypt для сервиса заявок и ретранслятора.
# Запускается контейнером certs до ticket, relay и nginx: во время docker build
# порт 80 снаружи недоступен, и проверка Let's Encrypt не проходит.
set -eu

email=${LETSENCRYPT_EMAIL:-}
ticket=${TICKET_DOMAIN:-}
relay=${RELAY_DOMAIN:-}
staging=${LETSENCRYPT_STAGING:-0}

valid_name() {
  printf '%s\n' "$1" | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$'
}

write_plain_http() {
  printf '%s\n' '# HTTPS нет. Трафик идёт по HTTP.' > /etc/letsencrypt/tls.conf
  cat > /etc/letsencrypt/http.conf <<'EOF'
server {
    listen 127.0.0.1:8080;
    listen [::1]:8080;
    server_name localhost;

    location = / {
        return 302 /swagger/tickets/index.html;
    }

    location /health {
        proxy_pass http://127.0.0.1:18080/health;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:18080;
    }

    location = /openapi/tickets.json {
        proxy_pass http://127.0.0.1:18080/openapi.json;
    }

    location /swagger/tickets/ {
        proxy_pass http://127.0.0.1:18080/swagger/;
    }

    location /swagger/ {
        return 404;
    }
}

server {
    listen 8080 default_server;
    listen [::]:8080 default_server;
    server_name _;
    return 444;
}
EOF
}

write_https_redirect() {
  public_names=$1
  cat > /etc/letsencrypt/http.conf <<EOF
server {
    listen 127.0.0.1:8080;
    listen [::1]:8080;
    server_name localhost;
    return 301 https://127.0.0.1\$request_uri;
}
EOF
  if [ -n "$public_names" ]; then
    cat >> /etc/letsencrypt/http.conf <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${public_names};
    return 301 https://\$host\$request_uri;
}

server {
    listen 80 default_server;
    listen [::]:80 default_server;
    listen 8080 default_server;
    listen [::]:8080 default_server;
    server_name _;
    return 444;
}
EOF
  else
    cat >> /etc/letsencrypt/http.conf <<'EOF'
server {
    listen 8080 default_server;
    listen [::]:8080 default_server;
    server_name _;
    return 444;
}
EOF
  fi
}

write_local_tls() {
  mkdir -p /etc/letsencrypt/live/local
  py=$(command -v python3 || command -v python || true)
  if [ -z "$py" ] || ! "$py" -c 'import cryptography' >/dev/null 2>&1; then
    echo "Локальный сертификат не собран. HTTPS выключен, HTTP на 127.0.0.1:8080 работает." >&2
    write_plain_http
    return 0
  fi
  if ! "$py" - <<'PY'
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
now = datetime.now(timezone.utc)
builder = (
    x509.CertificateBuilder()
    .subject_name(name)
    .issuer_name(name)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
)
if hasattr(builder, "not_valid_before_utc"):
    builder = builder.not_valid_before_utc(now - timedelta(minutes=5)).not_valid_after_utc(now + timedelta(days=30))
else:
    builder = builder.not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=30))
cert = builder.add_extension(
    x509.SubjectAlternativeName([
        x509.DNSName("localhost"),
        x509.IPAddress(ip_address("127.0.0.1")),
        x509.IPAddress(ip_address("::1")),
    ]),
    critical=False,
).sign(key, hashes.SHA256())
base = Path("/etc/letsencrypt/live/local")
base.joinpath("privkey.pem").write_bytes(
    key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
)
base.joinpath("fullchain.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
PY
  then
    echo "Локальный сертификат не собран. HTTPS выключен, HTTP на 127.0.0.1:8080 работает." >&2
    write_plain_http
    return 0
  fi
  cat > /etc/letsencrypt/tls.conf <<'EOF'
server {
    listen 127.0.0.1:443 ssl;
    listen [::1]:443 ssl;
    http2 on;
    server_name localhost;

    ssl_certificate /etc/letsencrypt/live/local/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/local/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    location = / {
        return 302 /swagger/tickets/index.html;
    }

    location /health {
        proxy_pass http://127.0.0.1:18080/health;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:18080;
    }

    location = /openapi/tickets.json {
        proxy_pass http://127.0.0.1:18080/openapi.json;
    }

    location /swagger/tickets/ {
        proxy_pass http://127.0.0.1:18080/swagger/;
    }
}
EOF
  write_https_redirect ""
  echo "HTTPS включён на 127.0.0.1:443. HTTP на порту 8080 только перенаправляет туда." >&2
}

if [ -z "$email" ] || [ -z "$ticket" ] || [ -z "$relay" ]; then
  echo "LETSENCRYPT_EMAIL, TICKET_DOMAIN и RELAY_DOMAIN не заданы. Let's Encrypt пропущен." >&2
  write_local_tls
  exit 0
fi

valid_name "$ticket" || {
  echo "TICKET_DOMAIN должен быть DNS-именем" >&2
  exit 1
}
valid_name "$relay" || {
  echo "RELAY_DOMAIN должен быть DNS-именем" >&2
  exit 1
}
printf '%s\n' "$email" | grep -Eq '^[^[:space:]@]+@[^[:space:]@]+$' || {
  echo "LETSENCRYPT_EMAIL выглядит неверно" >&2
  exit 1
}
if [ "$ticket" = "$relay" ]; then
  echo "TICKET_DOMAIN и RELAY_DOMAIN должны быть разными именами" >&2
  exit 1
fi

staging_arg=""
if [ "$staging" = "1" ]; then
  staging_arg="--staging"
fi

issue() {
  name=$1
  domain=$2
  # shellcheck disable=SC2086
  certbot certonly \
    --non-interactive \
    --agree-tos \
    --no-eff-email \
    --email "$email" \
    --standalone \
    --keep-until-expiring \
    --cert-name "$name" \
    -d "$domain" \
    $staging_arg
}

if ! issue ticket "$ticket" || ! issue relay "$relay"; then
  echo "Let's Encrypt не выпустил сертификат для ${ticket} и ${relay}. Имена должны существовать в DNS и указывать на эту машину, порт 80 открыт снаружи. Пока используется локальный сертификат на 127.0.0.1:443." >&2
  write_local_tls
  exit 0
fi

tmp=/etc/letsencrypt/tls.conf.new
cat > "$tmp" <<EOF
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name ${ticket};

    ssl_certificate /etc/letsencrypt/live/ticket/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ticket/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    location /health {
        proxy_pass http://127.0.0.1:18080/health;
    }

    location /api/ {
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_pass http://127.0.0.1:18080;
    }

    location / {
        return 444;
    }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name ${relay};

    ssl_certificate /etc/letsencrypt/live/relay/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/relay/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    location /health {
        proxy_pass http://127.0.0.1:18082/health;
    }

    location /api/ {
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_pass http://127.0.0.1:18082;
    }

    location / {
        return 444;
    }
}

server {
    listen 127.0.0.1:443 ssl;
    listen [::1]:443 ssl;
    http2 on;
    server_name localhost;

    ssl_certificate /etc/letsencrypt/live/ticket/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ticket/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    location = / {
        return 302 /swagger/tickets/index.html;
    }

    location /health {
        proxy_pass http://127.0.0.1:18080/health;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:18080;
    }

    location = /openapi/tickets.json {
        proxy_pass http://127.0.0.1:18080/openapi.json;
    }

    location /swagger/tickets/ {
        proxy_pass http://127.0.0.1:18080/swagger/;
    }
}
EOF
mv "$tmp" /etc/letsencrypt/tls.conf
write_https_redirect "${ticket} ${relay}"
echo "Сертификаты готовы: ${ticket}, ${relay}. HTTP перенаправляет на HTTPS."
