"""
nginx over SSH (no Qt here): the shell snippets the nginx tab runs, and parsing of what they print.

Everything that changes the server runs with sudo and is checked with `nginx -t` before nginx is
reloaded; a new site that fails the test is removed again, so a typo never takes the web server down.
Site names and domains are validated, every path is quoted, file contents travel base64-encoded.
"""
from __future__ import annotations

import base64
import re
import shlex

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
DOMAIN_RE = re.compile(r"^[A-Za-z0-9*][A-Za-z0-9.*-]{0,252}(\s+[A-Za-z0-9*][A-Za-z0-9.*-]{0,252})*$")
UPSTREAM_RE = re.compile(r"^(https?://)?[A-Za-z0-9.\-\[\]:]+(:\d{1,5})?(/[^\s;{}]*)?$")

LIST_SITES = r"""
for d in /etc/nginx/sites-available /etc/nginx/conf.d; do
  [ -d "$d" ] || continue
  for f in "$d"/*; do
    [ -f "$f" ] || continue
    en=0
    case "$d" in */conf.d) case "$f" in *.conf) en=1;; esac;; *) [ -e "/etc/nginx/sites-enabled/$(basename "$f")" ] && en=1;; esac
    names=$(grep -hoE '^[^#]*server_name[^;]+' "$f" 2>/dev/null | sed -E 's/.*server_name//' | tr -s ' \n' ' ' | head -c 300)
    ports=$(grep -hoE '^[^#]*listen[^;]+' "$f" 2>/dev/null | sed -E 's/.*listen//' | tr -s ' \n' ' ' | head -c 120)
    echo "SITE|$f|$en|$names|$ports"
  done
done
echo "VERSION|$(nginx -v 2>&1 | head -n1)"
echo "ACTIVE|$(systemctl is-active nginx 2>/dev/null)"
echo "CERTBOT|$(command -v certbot >/dev/null 2>&1 && echo 1 || echo 0)"
"""

CERTS = r"""
for f in /etc/letsencrypt/live/*/cert.pem; do
  [ -f "$f" ] || continue
  echo "CERT|$(basename "$(dirname "$f")")|$(openssl x509 -enddate -noout -in "$f" | cut -d= -f2)|$(openssl x509 -noout -ext subjectAltName -in "$f" 2>/dev/null | tail -n1 | tr -d ' ')"
done
"""

TEST = "nginx -t 2>&1"
RELOAD = "nginx -t 2>&1 && systemctl reload nginx && echo RELOADED"


def parse_sites(text: str) -> dict:
    sites, info = [], {"version": "", "active": "", "certbot": False}
    for line in text.splitlines():
        parts = line.split("|")
        if parts[0] == "SITE" and len(parts) >= 5:
            path = parts[1]
            sites.append({"path": path, "name": path.rsplit("/", 1)[-1], "enabled": parts[2] == "1",
                          "names": parts[3].strip(), "listen": parts[4].strip(),
                          "conf_d": "/conf.d/" in path})
        elif parts[0] == "VERSION":
            info["version"] = parts[1].replace("nginx version:", "").strip()
        elif parts[0] == "ACTIVE":
            info["active"] = parts[1].strip()
        elif parts[0] == "CERTBOT":
            info["certbot"] = parts[1].strip() == "1"
    info["sites"] = sites
    return info


def parse_certs(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        parts = line.split("|")
        if parts[0] == "CERT" and len(parts) >= 3:
            out.append({"name": parts[1], "expires": parts[2].strip(),
                        "domains": parts[3].replace("DNS:", "").replace(",", " ") if len(parts) > 3 else ""})
    return out


def _check_site(site: dict) -> str:
    path = site["path"]
    if not re.match(r"^/etc/nginx/(sites-available|conf\.d)/[A-Za-z0-9._-]+$", path):
        raise ValueError("unexpected path")
    return path


def show(site: dict) -> str:
    return f"cat {shlex.quote(_check_site(site))}"


def enable(site: dict) -> str:
    path = _check_site(site)
    link = "/etc/nginx/sites-enabled/" + site["name"]
    return f"ln -sf {shlex.quote(path)} {shlex.quote(link)} && {RELOAD} || (rm -f {shlex.quote(link)}; exit 1)"


def disable(site: dict) -> str:
    _check_site(site)
    link = "/etc/nginx/sites-enabled/" + site["name"]
    return f"rm -f {shlex.quote(link)} && {RELOAD}"


def logs(site: dict | None, lines: int = 200) -> str:
    n = max(20, min(2000, int(lines)))
    return (f"for f in /var/log/nginx/error.log /var/log/nginx/access.log; do [ -f \"$f\" ] && "
            f"echo \"===== $f\" && tail -n {n} \"$f\"; done")


def certbot(site: dict, email: str) -> str:
    domains = [d for d in site["names"].split() if d not in ("_", "localhost") and "*" not in d]
    if not domains:
        raise ValueError("no domain names in this site")
    for d in domains:
        if not re.match(r"^[A-Za-z0-9.-]+$", d):
            raise ValueError(f"bad domain {d}")
    mail = f"-m {shlex.quote(email)} --agree-tos" if email else "--register-unsafely-without-email --agree-tos"
    return f"certbot --nginx --non-interactive --redirect {mail} " + " ".join(f"-d {shlex.quote(d)}" for d in domains)


# ------------------------------------------------------------------------------- templates
def render(template: str, domains: str, target: str = "", root: str = "") -> str:
    domains = " ".join(domains.split())
    if not DOMAIN_RE.match(domains):
        raise ValueError("domain")
    if template == "proxy":
        if not UPSTREAM_RE.match(target):
            raise ValueError("upstream")
        upstream = target if target.startswith(("http://", "https://")) else "http://" + target
        return f"""server {{
    listen 80;
    listen [::]:80;
    server_name {domains};

    client_max_body_size 50m;

    location / {{
        proxy_pass {upstream};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 120s;
    }}
}}
"""
    if template == "static":
        root = root or f"/var/www/{domains.split()[0]}"
        if not re.match(r"^/[A-Za-z0-9._/-]+$", root):
            raise ValueError("root")
        return f"""server {{
    listen 80;
    listen [::]:80;
    server_name {domains};

    root {root};
    index index.html index.htm;

    location / {{
        try_files $uri $uri/ =404;
    }}
}}
"""
    if template == "redirect":
        if not re.match(r"^https?://[A-Za-z0-9.\-:/]+$", target):
            raise ValueError("target")
        return f"""server {{
    listen 80;
    listen [::]:80;
    server_name {domains};
    return 301 {target.rstrip('/')}$request_uri;
}}
"""
    raise ValueError(template)


def create_site(name: str, content: str, enable_now: bool = True) -> str:
    """Writes sites-available/<name>, links it, tests; a failing test removes it again."""
    if not NAME_RE.match(name):
        raise ValueError("name")
    path = f"/etc/nginx/sites-available/{name}"
    link = f"/etc/nginx/sites-enabled/{name}"
    data = base64.b64encode(content.encode()).decode()
    script = (f"if [ -e {shlex.quote(path)} ]; then echo 'EXISTS'; exit 3; fi; "
              f"mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled && "
              f"echo {data} | base64 -d > {shlex.quote(path)}")
    if enable_now:
        script += (f" && ln -sf {shlex.quote(path)} {shlex.quote(link)} && "
                   f"(nginx -t 2>&1 && systemctl reload nginx && echo CREATED) || "
                   f"(rm -f {shlex.quote(link)} {shlex.quote(path)}; echo 'ROLLED BACK'; exit 1)")
    return script
