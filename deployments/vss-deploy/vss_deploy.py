#!/usr/bin/env python3
"""Validate a VSS site.yaml, then create storage, one DataEngine pipeline, and the K8s app."""

from __future__ import annotations

import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parent
K8S_DIR = ROOT / "k8s"
WORK = ROOT / "deploy-work"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
SSL_CTX = ssl._create_unverified_context()
USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
STATUS_COLOR = {"PASS": "1;32", "FAIL": "1;31", "WARN": "1;33", "SKIP": "2;37"}


def paint(text: str, code: str) -> str:
    if not USE_COLOR or not code:
        return text
    return f"\033[{code}m{text}\033[0m"


def say(message: str, code: str = "") -> None:
    print(paint(message, code), flush=True)


class Check:
    def __init__(self, status: str, name: str, detail: str = ""):
        self.status = status
        self.name = name
        self.detail = detail


def run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    text = ANSI.sub("", (proc.stdout or "") + (proc.stderr or ""))
    return proc.returncode, text


def json_from_cli(text: str):
    start = min((i for i in (text.find("["), text.find("{")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError(text.strip()[:400] or "empty CLI output")
    return json.loads(text[start:])


def rows_of(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("results", "items"):
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    return []


def need(site: dict, *path: str):
    cur = site
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    if cur is None:
        return None
    if isinstance(cur, str) and not cur.strip():
        return None
    return cur


def https_config(site: dict) -> dict | None:
    """Return cert, key, and secret name when https is filled in. Empty means HTTP only."""
    https = site.get("https") or {}
    if not isinstance(https, dict):
        return None
    cert = str(https.get("cert") or "").strip()
    key = str(https.get("key") or "").strip()
    secret = str(https.get("secret") or "").strip() or "vss-tls"
    if not cert and not key:
        return None
    return {"cert": cert, "key": key, "secret": secret}


def with_ingress_tls(text: str, secret_name: str) -> str:
    docs = [doc for doc in yaml.safe_load_all(text) if doc]
    for doc in docs:
        if doc.get("kind") != "Ingress":
            continue
        hosts = [rule.get("host") for rule in (doc.get("spec") or {}).get("rules") or [] if rule.get("host")]
        if not hosts:
            continue
        doc.setdefault("spec", {})["tls"] = [{"hosts": hosts, "secretName": secret_name}]
    return yaml.safe_dump_all(docs, sort_keys=False)


def image_ref(value: str, tag) -> str:
    ref = str(value).strip()
    use = str(tag).strip() if tag else "latest"
    # registry:port/repo has a colon before the slash; a tag colon is after the last slash.
    name = ref.rsplit("/", 1)[-1]
    if ":" in name:
        return ref
    return f"{ref}:{use}"


def redact(text: str) -> str:
    return re.sub(
        r"(password|secret_key|jwt_secret|access_key|authorization|nvidia_api_key)(\"?\s*[:=]\s*\"?)[^\s\",}]+",
        r"\1\2<redacted>",
        text,
        flags=re.I,
    )


class Vms:
    def __init__(self, host: str, tenant: str, user: str, password: str):
        self.base = f"https://{host}/api/latest"
        self.token = self._login(host, tenant, user, password)

    def _login(self, host, tenant, user, password) -> str:
        req = urllib.request.Request(
            f"https://{host}/api/token/{tenant}/",
            data=json.dumps({"username": user, "password": password}).encode(),
            headers={"content-type": "application/json", "accept": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as resp:
            return json.loads(resp.read())["access"]

    def call(self, method: str, path: str, payload=None, timeout: int = 120):
        data = None if payload is None else json.dumps(payload).encode()
        headers = {"authorization": f"Bearer {self.token}", "accept": "application/json"}
        if data is not None:
            headers["content-type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=timeout) as resp:
                body = resp.read()
                return resp.status, json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw.decode(errors="replace")
            return exc.code, parsed


def ssh_run(host: str, user: str, password: str, script: str, timeout: int = 40) -> tuple[int, str]:
    """Run a script on the k8s node. Password is passed via SSH_ASKPASS, not argv."""
    WORK.mkdir(parents=True, exist_ok=True)
    askpass = WORK / "askpass.sh"
    askpass.write_text("#!/bin/sh\nprintf '%s\\n' \"$VSS_SSH_PASS\"\n")
    askpass.chmod(0o700)
    env = os.environ.copy()
    env["SSH_ASKPASS"] = str(askpass)
    env["SSH_ASKPASS_REQUIRE"] = "force"
    env["DISPLAY"] = env.get("DISPLAY") or "none"
    env["VSS_SSH_PASS"] = password
    proc = subprocess.run(
        [
            "ssh",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=8",
            "-o", "PreferredAuthentications=password",
            "-o", "NumberOfPasswordPrompts=1",
            f"{user}@{host}",
            "bash -s",
        ],
        input=script,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
    )
    text = ANSI.sub("", (proc.stdout or "") + (proc.stderr or ""))
    return proc.returncode, text


def auth_header(section: dict) -> str:
    """curl -H argument for an optional model Bearer token. Empty when unset."""
    token = str((section or {}).get("authorization") or "").strip()
    if not token:
        return ""
    value = token if token.lower().startswith("bearer ") else f"Bearer {token}"
    return f" -H {json.dumps('Authorization: ' + value)}"


def probe_from_node(site: dict) -> dict[str, str]:
    """HTTP status for each URL, measured on the k8s node. Keyed by label."""
    k8s = site.get("k8s") or {}
    targets = [
        ("s3", site["storage"]["s3_endpoint"], {}),
        ("ingest-vdb", site["storage"]["ingest_vdb_endpoint"], {}),
        ("backend-vdb", site["storage"]["backend_vdb_endpoint"], {}),
        ("cosmos", f"{site['runtime']['cosmos'].get('scheme', 'http')}://{site['runtime']['cosmos']['host']}:{site['runtime']['cosmos']['port']}/v1/models", site["runtime"]["cosmos"]),
        ("embed", f"{site['runtime']['embed'].get('scheme', 'http')}://{site['runtime']['embed']['host']}:{site['runtime']['embed']['port']}/v1/models", site["runtime"]["embed"]),
        ("yolo", f"{site['runtime']['yolo'].get('scheme', 'http')}://{site['runtime']['yolo']['host']}:{site['runtime']['yolo']['port']}/healthz", site["runtime"]["yolo"]),
    ]
    lines = ["set +e"]
    for label, url, section in targets:
        lines.append(
            f"code=$(curl -m 5 -sk -o /dev/null -w '%{{http_code}}'{auth_header(section)} {json.dumps(url)} || echo fail); "
            f"echo PROBE {label} \"$code\""
        )
    code, out = ssh_run(str(k8s["host"]), str(k8s["user"]), str(k8s["password"]), "\n".join(lines) + "\n")
    found = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "PROBE":
            found[parts[1]] = parts[2]
    if code != 0 and not found:
        raise RuntimeError(redact(out)[:300] or f"ssh exit {code}")
    return found


def manifest_ok(url: str) -> int:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=15) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return 0


class Checks(list):
    """Print each result as soon as it is recorded."""

    def append(self, item: Check):
        super().append(item)
        status = paint(f"{item.status:<5}", STATUS_COLOR.get(item.status, ""))
        detail = paint(f"  {item.detail}", "2") if item.detail else ""
        print(f"{status} {item.name}{detail}", flush=True)


def step(message: str) -> None:
    say(f"… {message}", "36")


def summary(checks: list[Check]) -> int:
    failed = sum(1 for c in checks if c.status == "FAIL")
    warned = sum(1 for c in checks if c.status == "WARN")
    code = "1;31" if failed else "1;33" if warned else "1;32"
    say(f"\n{failed} failed, {warned} warnings", code)
    return failed


def kube_env(site: dict) -> dict:
    env = os.environ.copy()
    if not env.get("KUBECONFIG") and need(site, "site", "kubeconfig"):
        env["KUBECONFIG"] = str(site["site"]["kubeconfig"])
    return env


def kubectl(env: dict, *args: str, timeout: int = 30) -> tuple[int, str]:
    proc = subprocess.run(["kubectl", *args], text=True, capture_output=True, timeout=timeout, env=env)
    return proc.returncode, ANSI.sub("", (proc.stdout or "") + (proc.stderr or ""))


def validate(site: dict) -> tuple[list[Check], dict]:
    say("\nVSS deploy validation\n", "1")
    checks = Checks()
    ctx: dict = {"site": site, "env": kube_env(site)}

    required = [
        ("site.namespace", ("site", "namespace")),
        ("site.ingress_domain", ("site", "ingress_domain")),
        ("vms.host", ("vms", "host")),
        ("vms.user", ("vms", "user")),
        ("vms.password", ("vms", "password")),
        ("vms.tenant", ("vms", "tenant")),
        ("vms.view_policy", ("vms", "view_policy")),
        ("storage.group", ("storage", "group")),
        ("dataengine.cluster", ("dataengine", "cluster")),
        ("dataengine.broker", ("dataengine", "broker")),
        ("dataengine.topic", ("dataengine", "topic")),
        ("dataengine.registry", ("dataengine", "registry")),
        ("dataengine.secret", ("dataengine", "secret")),
        ("dataengine.pipeline", ("dataengine", "pipeline")),
        ("storage.user", ("storage", "user")),
        ("storage.password", ("storage", "password")),
        ("storage.s3_endpoint", ("storage", "s3_endpoint")),
        ("storage.ingest_vdb_endpoint", ("storage", "ingest_vdb_endpoint")),
        ("storage.backend_vdb_endpoint", ("storage", "backend_vdb_endpoint")),
        ("runtime.jwt_secret", ("runtime", "jwt_secret")),
        ("k8s.host", ("k8s", "host")),
        ("k8s.user", ("k8s", "user")),
        ("k8s.password", ("k8s", "password")),
    ]
    missing = [label for label, path in required if need(site, *path) is None]
    if missing:
        checks.append(Check("FAIL", "site.yaml required fields", ", ".join(missing)))
    else:
        checks.append(Check("PASS", "site.yaml required fields"))

    https = https_config(site)
    if https is None:
        checks.append(Check("SKIP", "https", "cert and key empty; ingress stays HTTP"))
    else:
        missing_files = [path for path in (https["cert"], https["key"]) if not Path(path).is_file()]
        if not https["cert"] or not https["key"]:
            checks.append(Check("FAIL", "https", "set both https.cert and https.key"))
        elif missing_files:
            checks.append(Check("FAIL", "https", "missing " + ", ".join(missing_files)))
        else:
            checks.append(Check("PASS", "https", f"secret {https['secret']}"))

    pipeline = str(need(site, "dataengine", "pipeline") or "")
    if pipeline and not 2 <= len(pipeline) <= 40:
        checks.append(Check("FAIL", "pipeline name length", f"{pipeline} is {len(pipeline)} chars; VAST allows 2-40"))
    elif pipeline:
        checks.append(Check("PASS", "pipeline name length", pipeline))

    step("vastde")
    if shutil.which("vastde") is None:
        checks.append(Check("FAIL", "vastde installed", "not on PATH"))
        return checks, ctx
    code, ver = run(["vastde", "version"])
    checks.append(Check("PASS" if code == 0 else "FAIL", "vastde installed", ver.strip().splitlines()[-1][:80]))

    code, cfg = run(["vastde", "config", "view"])
    tenant_line = ""
    user_line = ""
    vms_line = ""
    for line in cfg.splitlines():
        if "password" in line.lower():
            continue
        if line.lower().startswith("tenant name:"):
            tenant_line = line.split(":", 1)[1].strip()
        elif line.lower().startswith("username:"):
            user_line = line.split(":", 1)[1].strip()
        elif line.lower().startswith("vms url:"):
            vms_line = line.split(":", 1)[1].strip()
    want_tenant = str(need(site, "vms", "tenant") or "")
    want_user = str(need(site, "vms", "user") or "")
    want_host = str(need(site, "vms", "host") or "")
    got_host = urlparse(vms_line if "://" in vms_line else f"https://{vms_line}").hostname or ""
    checks.append(Check(
        "PASS" if tenant_line == want_tenant else "FAIL",
        "vastde tenant",
        f"config={tenant_line or '<empty>'} yaml={want_tenant}",
    ))
    checks.append(Check(
        "PASS" if user_line == want_user else "FAIL",
        "vastde username",
        f"config={user_line or '<empty>'} yaml={want_user}",
    ))
    checks.append(Check(
        "PASS" if got_host == want_host else "FAIL",
        "vastde VMS",
        f"config={got_host or '<empty>'} yaml={want_host}",
    ))

    step("kubectl")
    if shutil.which("kubectl") is None:
        checks.append(Check("FAIL", "kubectl installed", "not on PATH"))
    else:
        env = ctx["env"]
        kc = env.get("KUBECONFIG") or "(default)"
        code, out = kubectl(env, "cluster-info")
        api = ""
        for line in out.splitlines():
            if "control plane" in line or "Kubernetes control plane" in line:
                api = line.strip()
        checks.append(Check("PASS" if code == 0 else "FAIL", "kubectl cluster", f"{kc}; {api or out.strip()[:120]}"))
        ns = str(need(site, "site", "namespace") or "")
        code, phase = kubectl(env, "get", "ns", ns, "-o", "jsonpath={.status.phase}")
        checks.append(Check("PASS" if code == 0 and phase == "Active" else "FAIL", "namespace", f"{ns} {phase or 'missing'}"))

    cluster = str(need(site, "dataengine", "cluster") or "")
    step(f"DataEngine cluster {cluster}")
    code, out = run(["vastde", "compute-clusters", "get", cluster, "-o", "json"], timeout=40)
    namespaces = []
    if code != 0:
        checks.append(Check("FAIL", "DataEngine cluster", redact(out)[:200]))
    else:
        try:
            info = json_from_cli(out)
            namespaces = info.get("namespaces") or []
            ctx["cluster"] = info
        except Exception as exc:
            checks.append(Check("FAIL", "DataEngine cluster", str(exc)[:200]))
            info = {}
        attached = str(need(site, "site", "namespace") or "") in namespaces
        checks.append(Check(
            "PASS" if attached else "FAIL",
            "namespace attached",
            f"{cluster} namespaces={namespaces}",
        ))

    registry = str(need(site, "dataengine", "registry") or "")
    step(f"registry {registry}")
    code, out = run(["vastde", "container-registries", "get", registry, "-o", "json"], timeout=40)
    reg_url = ""
    if code != 0:
        checks.append(Check("FAIL", "container registry", redact(out)[:200]))
    else:
        try:
            reg = json_from_cli(out)
            ctx["registry"] = reg
            reg_url = str(reg.get("url") or "")
            linked = (reg.get("primary_kubernetes_cluster") or {}).get("kubernetes_cluster_vrn", "")
            ok = linked.endswith(":" + cluster) or linked.endswith("/" + cluster)
            checks.append(Check(
                "PASS" if ok else "FAIL",
                "registry linked",
                f"{registry} {reg_url} -> {linked or '<none>'}",
            ))
        except Exception as exc:
            checks.append(Check("FAIL", "container registry", str(exc)[:200]))

    step("buckets and topics")
    code, out = run(["vastde", "buckets", "list", "-o", "json"], timeout=40)
    buckets = []
    if code != 0:
        checks.append(Check("FAIL", "buckets list", redact(out)[:200]))
    else:
        try:
            buckets = rows_of(json_from_cli(out))
        except Exception as exc:
            checks.append(Check("FAIL", "buckets list", str(exc)[:200]))
    broker = str(need(site, "dataengine", "broker") or "")
    broker_row = next((b for b in buckets if broker in (b.get("bucket"), b.get("s3_bucket_name"), b.get("name"))), None)
    protocols = [str(p).upper() for p in (broker_row or {}).get("protocols") or []]
    checks.append(Check(
        "PASS" if broker_row and "KAFKA" in protocols else "FAIL",
        "broker",
        f"{broker} protocols={protocols or '<missing>'}",
    ))

    planned = [
        need(site, "storage", "buckets", "chunks"),
        need(site, "storage", "buckets", "segments"),
        need(site, "storage", "buckets", "database"),
    ]
    existing_names = {b.get("bucket") or b.get("s3_bucket_name") for b in buckets}
    for name in planned:
        if not name:
            checks.append(Check("FAIL", "bucket name", "missing in site.yaml"))
        elif name in existing_names:
            checks.append(Check("PASS", f"bucket {name}", "already exists"))
        else:
            checks.append(Check("PASS", f"bucket {name}", "will be created"))

    chunks_name = str(need(site, "storage", "buckets", "chunks") or "")
    segments_name = str(need(site, "storage", "buckets", "segments") or "")
    suffix = str(need(site, "storage", "output_bucket_suffix") or "-segments")
    if chunks_name and segments_name:
        derived = f"{chunks_name}{suffix}"
        checks.append(Check(
            "PASS" if derived == segments_name else "FAIL",
            "segments bucket",
            f"{chunks_name}+{suffix}={derived}; yaml={segments_name}",
        ))
    embed_cfg = (site.get("runtime") or {}).get("embed") or {}
    local_nim = embed_cfg.get("local_nim", True)
    if isinstance(local_nim, str):
        local_nim = local_nim.strip().lower() not in ("false", "0", "no")
    api_key = str(embed_cfg.get("nvidia_api_key") or "").strip()
    if local_nim:
        checks.append(Check("SKIP", "nvidia api key", "embed.local_nim is true"))
    else:
        checks.append(Check(
            "PASS" if api_key else "FAIL",
            "nvidia api key",
            "required when embed.local_nim is false",
        ))

    code, out = run(["vastde", "topics", "list", "--database-name", broker, "-o", "json"], timeout=40)
    topic = str(need(site, "dataengine", "topic") or "")
    topic_names = []
    if code != 0:
        checks.append(Check("FAIL", "topics list", redact(out)[:200]))
    else:
        try:
            topic_names = [t.get("name") for t in rows_of(json_from_cli(out))]
        except Exception as exc:
            checks.append(Check("FAIL", "topics list", str(exc)[:200]))
        if topic in topic_names:
            checks.append(Check("PASS", f"topic {topic}", "already exists"))
        elif topic:
            checks.append(Check("PASS", f"topic {topic}", "will be created"))

    code, out = run(["vastde", "triggers", "list", "-o", "json"], timeout=40)
    trigger_rows = []
    if code == 0:
        try:
            trigger_rows = rows_of(json_from_cli(out))
        except Exception:
            trigger_rows = []
    broker_urls = []
    for trig in trigger_rows:
        url = ((trig.get("broker") or {}).get("url")) or ""
        if url:
            broker_urls.append(url)
    s3_host = urlparse(str(need(site, "storage", "s3_endpoint") or "")).hostname
    if broker_urls and s3_host:
        hosts = {urlparse("kafka://" + u if "://" not in u else u).hostname for u in broker_urls}
        checks.append(Check(
            "PASS" if s3_host in hosts else "WARN",
            "s3 endpoint host",
            f"yaml={s3_host} broker={sorted(hosts)}",
        ))
    else:
        checks.append(Check("SKIP", "s3 endpoint host", "no existing trigger carries a broker url"))

    ingest = str(need(site, "storage", "ingest_vdb_endpoint") or "")
    backend = str(need(site, "storage", "backend_vdb_endpoint") or "")

    k8s_host = need(site, "k8s", "host")
    if k8s_host and need(site, "k8s", "user") and need(site, "k8s", "password"):
        step(f"probe VIPs and models from {k8s_host}")
        try:
            probed = probe_from_node(site)
        except Exception as exc:
            checks.append(Check("FAIL", "k8s host probe", redact(str(exc))[:200]))
            probed = {}
        urls = {
            "s3": site["storage"]["s3_endpoint"],
            "ingest-vdb": ingest,
            "backend-vdb": backend,
            "cosmos": f"{site['runtime']['cosmos']['host']}:{site['runtime']['cosmos']['port']}/v1/models",
            "embed": f"{site['runtime']['embed']['host']}:{site['runtime']['embed']['port']}/v1/models",
            "yolo": f"{site['runtime']['yolo']['host']}:{site['runtime']['yolo']['port']}/healthz",
        }
        for label, shown in urls.items():
            status = probed.get(label)
            if not status or status in ("000", "fail"):
                checks.append(Check("FAIL", f"probe {label}", f"from {k8s_host} no response {shown}"))
            elif label in ("cosmos", "embed", "yolo") and status != "200":
                checks.append(Check("WARN", f"probe {label}", f"from {k8s_host} HTTP {status} {shown}"))
            else:
                checks.append(Check("PASS", f"probe {label}", f"from {k8s_host} HTTP {status} {shown}"))
    elif not missing:
        checks.append(Check("FAIL", "k8s host", "set k8s.host, k8s.user, and k8s.password to probe VIPs and models"))

    step("container images")
    if reg_url:
        scheme_urls = [reg_url if "://" in reg_url else f"https://{reg_url}"]
        functions = (site.get("images") or {}).get("functions") or {}
        for name, spec in functions.items():
            spec = spec or {}
            artifact = spec.get("artifact")
            tag = spec.get("tag") or "latest"
            if not artifact:
                checks.append(Check("FAIL", f"function image {name}", "artifact empty"))
                continue
            code = manifest_ok(f"{scheme_urls[0]}/v2/{artifact}/manifests/{tag}")
            checks.append(Check("PASS" if code == 200 else "FAIL", f"function image {name}", f"{artifact}:{tag} HTTP {code}"))
        apps = (site.get("images") or {}).get("kubernetes") or {}
        for name, ref in apps.items():
            if not ref:
                checks.append(Check("FAIL", f"k8s image {name}", "empty"))
                continue
            full = image_ref(str(ref), None)
            path = full.split("/", 1)[1]
            repo, tag = path.rsplit(":", 1)
            code = manifest_ok(f"{scheme_urls[0]}/v2/{repo}/manifests/{tag}")
            checks.append(Check("PASS" if code == 200 else "FAIL", f"k8s image {name}", f"{full} HTTP {code}"))

    triggers = site.get("pipeline", {}).get("triggers") or []
    functions = site.get("pipeline", {}).get("functions") or []
    links = site.get("pipeline", {}).get("links") or []
    tnames = {t.get("name") for t in triggers}
    fnames = {f.get("name") for f in functions}
    chunks = need(site, "storage", "buckets", "chunks")
    segments = need(site, "storage", "buckets", "segments")
    element = [t for t in triggers if str(t.get("type", "")).lower() == "element"]
    if len(element) >= 1 and element[0].get("bucket") != chunks:
        checks.append(Check("FAIL", "chunk trigger bucket", f"{element[0].get('bucket')} != {chunks}"))
    elif element:
        checks.append(Check("PASS", "chunk trigger bucket", chunks))
    if len(element) >= 2 and element[1].get("bucket") != segments:
        checks.append(Check("FAIL", "segment trigger bucket", f"{element[1].get('bucket')} != {segments}"))
    elif len(element) >= 2:
        checks.append(Check("PASS", "segment trigger bucket", segments))
    bad_links = []
    for link in links:
        if not isinstance(link, list) or len(link) != 2:
            bad_links.append(str(link))
            continue
        src, dst = link
        if src not in tnames and src not in fnames:
            bad_links.append(src)
        if dst not in fnames:
            bad_links.append(dst)
    checks.append(Check("FAIL" if bad_links else "PASS", "pipeline links", ", ".join(bad_links) or f"{len(links)} links"))

    step(f"VMS login {site['vms']['tenant']}")
    try:
        vms = Vms(site["vms"]["host"], site["vms"]["tenant"], site["vms"]["user"], site["vms"]["password"])
        ctx["vms"] = vms
        checks.append(Check("PASS", "VMS login", site["vms"]["tenant"]))
        code, data = vms.call("GET", f"/tenants/?name={site['vms']['tenant']}")
        tenant_rows = rows_of(data)
        tenant_id = tenant_rows[0].get("id") if code == 200 and tenant_rows else None
        ctx["tenant_id"] = tenant_id
        checks.append(Check("PASS" if tenant_id else "FAIL", "tenant id", str(tenant_id)))
        code, data = vms.call("GET", f"/viewpolicies/?name={site['vms']['view_policy']}")
        pols = [p for p in rows_of(data) if p.get("name") == site["vms"]["view_policy"]]
        ctx["policy_id"] = pols[0].get("id") if pols else None
        checks.append(Check("PASS" if ctx["policy_id"] else "FAIL", "view policy", f"{site['vms']['view_policy']} id={ctx['policy_id']}"))
        group_name = site["storage"]["group"]
        code, data = vms.call("GET", f"/groups/?name={group_name}")
        groups = [g for g in rows_of(data) if g.get("name") == group_name]
        ctx["group"] = group_name
        ctx["gid"] = groups[0].get("gid") if groups else None
        checks.append(Check("PASS" if ctx["gid"] else "FAIL", "user group", f"{group_name} gid={ctx['gid']}"))
        code, data = vms.call("GET", f"/users/?name={site['storage']['user']}")
        users = [u for u in rows_of(data) if u.get("name") == site["storage"]["user"]]
        ctx["user"] = users[0] if users else None
        if users:
            member = group_name in (users[0].get("groups") or [])
            detail = f"member of {group_name}" if member else f"will be attached to {group_name}"
        else:
            detail = f"will be created in {group_name}"
        checks.append(Check("PASS", f"user {site['storage']['user']}", detail))
    except Exception as exc:
        checks.append(Check("FAIL", "VMS login", redact(str(exc))[:200]))

    return checks, ctx


def save_keys(access_key: str, secret_key: str) -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    path = WORK / "storage-keys.yaml"
    path.write_text(yaml.safe_dump({"access_key": access_key, "secret_key": secret_key}))
    path.chmod(0o600)


def load_keys():
    path = WORK / "storage-keys.yaml"
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text())
    if data and data.get("access_key") and data.get("secret_key"):
        return data
    return None


def ensure_user_group(vms: Vms, user: dict, group_name: str, gid) -> dict:
    """Membership is the gids list. The groups field on create is ignored by VMS."""
    if group_name in (user.get("groups") or []):
        say(f"  {user.get('name')} is in {group_name}", "32")
        return user
    gids = [int(item) for item in (user.get("gids") or [])]
    if int(gid) not in gids:
        gids.append(int(gid))
    say(f"attach {user.get('name')} to {group_name}", "1;36")
    code, data = vms.call("PATCH", f"/users/{user['id']}/", {"gids": gids})
    if code not in (200, 201):
        raise SystemExit(f"attach group failed HTTP {code}: {redact(str(data)[:400])}")
    if group_name not in (data.get("groups") or []):
        raise SystemExit(f"user {user.get('name')} is not a member of {group_name}")
    say(f"  groups {data.get('groups')}", "32")
    return data


def deploy_storage(ctx: dict) -> dict:
    site = ctx["site"]
    vms: Vms = ctx["vms"]
    user_name = site["storage"]["user"]
    group_name = site["storage"]["group"]
    user = ctx.get("user")
    if user is None:
        say(f"\ncreate user {user_name} in {group_name}", "1;36")
        code, data = vms.call("POST", "/users/", {
            "name": user_name,
            "gids": [int(ctx["gid"])],
            "password": site["storage"]["password"],
        })
        if code not in (200, 201):
            raise SystemExit(f"create user failed HTTP {code}: {redact(json.dumps(data)[:400])}")
        user = data
        say(f"  user id {user.get('id')}", "32")
    user = ensure_user_group(vms, user, group_name, ctx["gid"])
    keys = load_keys()
    existing = user.get("access_keys") or []
    if keys is None:
        if existing:
            raise SystemExit(
                f"user {user_name} already has an access key and deploy-work/storage-keys.yaml is missing. "
                "Refusing to create another key."
            )
        say("create access key", "1;36")
        code, data = vms.call("POST", f"/users/{user['id']}/access_keys/", {})
        if code not in (200, 201) or "secret_key" not in data:
            raise SystemExit(f"access key failed HTTP {code}: {redact(str(data)[:300])}")
        save_keys(data["access_key"], data["secret_key"])
        keys = data
        say("  saved deploy-work/storage-keys.yaml", "32")
    else:
        say("reuse deploy-work/storage-keys.yaml", "32")
    say(f"  {user_name} access key: {keys['access_key']}", "1;32")
    say(f"  {user_name} secret key: {keys['secret_key']}", "1;32")

    buckets = site["storage"]["buckets"]
    specs = [
        (buckets["chunks"], ["S3"]),
        (buckets["segments"], ["S3"]),
        (buckets["database"], ["S3", "DATABASE"]),
    ]
    for bucket, protocols in specs:
        code, listed = vms.call("GET", f"/views/?tenant_id={ctx['tenant_id']}")
        have = {row.get("bucket") for row in rows_of(listed)}
        if bucket in have:
            say(f"bucket {bucket} exists", "32")
            continue
        say(f"create bucket {bucket} {protocols}", "1;36")
        code, data = vms.call("POST", "/views/", {
            "path": f"/{bucket}",
            "create_dir": True,
            "protocols": protocols,
            "bucket": bucket,
            "bucket_owner": user_name,
            "policy_id": ctx["policy_id"],
            "tenant_id": ctx["tenant_id"],
        })
        if code not in (200, 201):
            raise SystemExit(f"create view {bucket} failed HTTP {code}: {redact(str(data)[:400])}")
        say(f"  view id {data.get('id')}", "32")

    topic = site["dataengine"]["topic"]
    broker = site["dataengine"]["broker"]
    code, out = run(["vastde", "topics", "list", "--database-name", broker, "-o", "json"])
    names = [t.get("name") for t in rows_of(json_from_cli(out))] if code == 0 else []
    if topic in names:
        say(f"topic {topic} exists", "32")
    else:
        say(f"create topic {topic} on {broker}", "1;36")
        code, data = vms.call("POST", "/topics/", {"database_name": broker, "name": topic})
        if code not in (200, 201):
            raise SystemExit(f"create topic failed HTTP {code}: {redact(str(data)[:400])}")
    return keys


def cli_exists(kind: str, name: str) -> bool:
    code, out = run(["vastde", kind, "list", "-o", "json"], timeout=40)
    if code != 0:
        raise SystemExit(f"vastde {kind} list failed: {redact(out)[:300]}")
    text = out.lower()
    if "no " in text and "available" in text:
        return False
    try:
        data = rows_of(json_from_cli(out))
    except Exception:
        return False
    return any(row.get("name") == name for row in data)


def run_ok(cmd: list[str], timeout: int = 120) -> None:
    step(" ".join(cmd[:6]))
    code, out = run(cmd, timeout=timeout)
    text = redact(out).rstrip()
    if text:
        print(text, flush=True)
    if re.search(r"already exists|409\s+Conflict", out, re.I):
        say("  already exists", "32")
        return
    if code != 0 or re.search(r"creation failed|failed to|401 Unauthorized|unknown flag", out, re.I):
        raise SystemExit(f"command failed: {' '.join(cmd[:4])}")


def deploy_dataengine(site: dict, keys: dict) -> None:
    de = site["dataengine"]
    broker = de["broker"]
    topic = de["topic"]
    registry = de["registry"]
    for trig in site["pipeline"]["triggers"]:
        name = trig["name"]
        if cli_exists("triggers", name):
            say(f"trigger {name} exists", "32")
            continue
        cmd = ["vastde", "triggers", "create", "--name", name, "--broker-name", broker, "--broker-type", "Internal", "--topic", topic]
        kind = str(trig.get("type", "")).lower()
        if kind == "element":
            cmd += ["--type", "Element", "--events", "ObjectCreated:*", "--source-bucket", trig["bucket"]]
            if trig.get("suffix"):
                cmd += ["--name-suffix", str(trig["suffix"])]
            if trig.get("prefix"):
                cmd += ["--name-prefix", str(trig["prefix"])]
        elif kind == "schedule":
            cmd += ["--type", "Schedule", "--cron-schedule", str(trig["cron"])]
        else:
            raise SystemExit(f"unknown trigger type for {name}")
        say(f"create trigger {name}", "1;36")
        run_ok(cmd)

    images = site["images"]["functions"]
    for fn in site["pipeline"]["functions"]:
        name = fn["name"]
        if cli_exists("functions", name):
            say(f"function {name} exists", "32")
            continue
        spec = images.get(name) or {}
        tag = spec.get("tag") or "latest"
        say(f"create function {name} {spec.get('artifact')}:{tag}", "1;36")
        run_ok([
            "vastde", "functions", "create",
            "--name", name,
            "--container-registry", registry,
            "--artifact-source", spec["artifact"],
            "--artifact-type", "image",
            "--image-tag", str(tag),
            "--publish",
        ])

    secret_path = write_ingest_secret(site, keys)
    manifest_path = write_pipeline_manifest(site)
    pipeline = de["pipeline"]
    if cli_exists("pipelines", pipeline):
        say(f"deploy existing pipeline {pipeline}", "1;36")
        run_ok(["vastde", "pipelines", "deploy", pipeline], timeout=180)
        return
    say(f"create pipeline {pipeline}", "1;36")
    run_ok([
        "vastde", "pipelines", "create",
        "--name", pipeline,
        "--config", f"@{manifest_path}",
        "--secret-file", str(secret_path),
        "--deploy",
    ], timeout=180)


def secret_entries(site: dict, keys: dict) -> list[tuple[str, str]]:
    st = site["storage"]
    rt = site["runtime"]
    cosmos = rt["cosmos"]
    embed = rt["embed"]
    yolo = rt["yolo"]
    pairs = [
        ("s3accesskey", keys["access_key"]),
        ("s3secretkey", keys["secret_key"]),
        ("s3endpoint", st["s3_endpoint"]),
        ("cosmos_host", cosmos["host"]),
        ("cosmos_port", cosmos["port"]),
        ("cosmoshttpscheme", cosmos.get("scheme") or "http"),
        ("cosmos_authorization", cosmos.get("authorization") or ""),
        ("cosmos_model", cosmos["model"]),
        ("cosmos_max_tokens", cosmos.get("max_tokens", 6000)),
        ("cosmos_temperature", cosmos.get("temperature", 0.2)),
        ("embedding_local_nim", embed.get("local_nim", True)),
        ("embeddinghost", embed["host"]),
        ("embeddingport", embed["port"]),
        ("embeddinghttpscheme", embed.get("scheme") or "http"),
        ("embedding_authorization", embed.get("authorization") or ""),
        ("nvidia_api_key", embed.get("nvidia_api_key") or ""),
        ("embeddingmodel", embed["model"]),
        ("embeddingdimensions", embed["dimensions"]),
        ("visual_embedding_enabled", True),
        ("visual_embedding_model", embed["model"]),
        ("visual_embedding_dimensions", embed["dimensions"]),
        ("max_video_size_mb", 100),
        ("scenario", "general"),
        ("vdbendpoint", st["ingest_vdb_endpoint"]),
        ("vdbbucket", st["buckets"]["database"]),
        ("vdbschema", st["schema"]),
        ("vdbaccesskey", keys["access_key"]),
        ("vdbsecretkey", keys["secret_key"]),
        ("vdbcollection", st["collection"]),
        ("vdbpromptscollection", st["prompts_collection"]),
        ("suggestions_max_segments", 48),
        ("suggestions_search_count", 10),
        ("suggestions_events_count", 30),
        ("suggestions_max_events_per_video", 3),
        ("suggestions_lookback_hours", 168),
        ("segment_duration", rt.get("segment_duration", 5)),
        ("output_codec", "libx264"),
        ("output_format", "mp4"),
        ("output_bucket_suffix", st.get("output_bucket_suffix") or "-segments"),
        ("yolo_infer_host", yolo["host"]),
        ("yolo_infer_port", yolo["port"]),
        ("detector_authorization", yolo.get("authorization") or ""),
        ("yolo_conf", yolo.get("conf", 0.4)),
        ("yolo_model", yolo["model"]),
        ("yolo_presign_ttl", 3600),
        ("detection_sidecar_prefix", "detections/"),
        ("detection_store_frames", True),
    ]
    return [(k, v) for k, v in pairs if v is not None and str(v) != ""]


def write_ingest_secret(site: dict, keys: dict) -> Path:
    WORK.mkdir(parents=True, exist_ok=True)
    de = site["dataengine"]
    lines = [
        f"name: {de['secret']}",
        f"kubernetes_cluster_vrn: vast:dataengine:kubernetes-clusters:{de['cluster']}",
        f"namespace: {site['site']['namespace']}",
        "entries:",
    ]
    for key, value in secret_entries(site, keys):
        lines.append(f"  - key: {key}")
        lines.append(f"    value: {json.dumps(str(value))}")
    path = WORK / "vss-secret.yaml"
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)
    return path


def write_pipeline_manifest(site: dict) -> Path:
    de = site["dataengine"]
    triggers = site["pipeline"]["triggers"]
    functions = site["pipeline"]["functions"]
    tnames = {t["name"] for t in triggers}
    topic_vrn = f"vast:dataengine:topics:{de['broker']}/{de['topic']}"
    deployments = []
    for fn in functions:
        timeout = 300 if "suggester" in fn["name"] else 600
        deployments.append({
            "name": fn["name"],
            "function_vrn": f"vast:dataengine:functions:{fn['name']}",
            "revision": 1,
            "config": {"log_level": "INFO"},
            "resources": {
                "min_concurrency": 1,
                "max_concurrency": int(fn.get("max_concurrency", 1)),
                "min_cpu": fn["min_cpu"],
                "max_cpu": fn["max_cpu"],
                "min_memory": fn["min_memory"],
                "max_memory": fn["max_memory"],
                "timeout": timeout,
            },
        })
    links = []
    for src, dst in site["pipeline"]["links"]:
        link = {
            "source": [src],
            "destination": [dst],
            "config": {"retries": 3, "events_order": "unordered"},
        }
        if src in tnames:
            link["topic"] = topic_vrn
        links.append(link)
    doc = {
        "namespace": site["site"]["namespace"],
        "kubernetes_cluster_vrn": f"vast:dataengine:kubernetes-clusters:{de['cluster']}",
        "manifest": {
            "triggers": [{"name": t["name"], "vrn": f"vast:dataengine:triggers:{t['name']}"} for t in triggers],
            "function_deployments": deployments,
            "links": links,
            "config": {"secrets": [de["secret"]], "environment_variables": []},
        },
    }
    path = WORK / "pipeline.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


def write_backend_secret(site: dict, keys: dict) -> None:
    st = site["storage"]
    rt = site["runtime"]
    cosmos = rt["cosmos"]
    embed = rt["embed"]
    ssl = "true" if str(st["s3_endpoint"]).startswith("https") else "false"
    local_nim = "true" if embed.get("local_nim", True) else "false"
    config = f"""    vdb_endpoint: {json.dumps(st["backend_vdb_endpoint"])}
    vdb_bucket: {json.dumps(st["buckets"]["database"])}
    vdb_schema: {json.dumps(st["schema"])}
    vdb_collection: {json.dumps(st["collection"])}
    vdb_prompts_collection: {json.dumps(st["prompts_collection"])}
    vdb_access_key: {json.dumps(keys["access_key"])}
    vdb_secret_key: {json.dumps(keys["secret_key"])}
    s3_endpoint: {json.dumps(st["s3_endpoint"])}
    s3_access_key: {json.dumps(keys["access_key"])}
    s3_secret_key: {json.dumps(keys["secret_key"])}
    s3_upload_bucket: {json.dumps(st["buckets"]["chunks"])}
    s3_segments_bucket: {json.dumps(st["buckets"]["segments"])}
    s3_region: "us-east-1"
    s3_use_ssl: {ssl}
    embedding_local_nim: {local_nim}
    embedding_host: {json.dumps(embed["host"])}
    embedding_port: {embed["port"]}
    embedding_http_scheme: {json.dumps(embed.get("scheme") or "http")}
    embedding_authorization: {json.dumps(embed.get("authorization") or "")}
    nvidia_api_key: {json.dumps(embed.get("nvidia_api_key") or "")}
    embedding_model: {json.dumps(embed["model"])}
    embedding_dimensions: {embed["dimensions"]}
    visual_embedding_model: {json.dumps(embed["model"])}
    visual_embedding_dimensions: {embed["dimensions"]}
    hybrid_text_weight: 0.6
    cosmos_host: {json.dumps(cosmos["host"])}
    cosmos_port: {cosmos["port"]}
    cosmoshttpscheme: {json.dumps(cosmos.get("scheme") or "http")}
    cosmos_authorization: {json.dumps(cosmos.get("authorization") or "")}
    cosmos_model: {json.dumps(cosmos["model"])}
    cosmos_temperature: {cosmos.get("temperature", 0.2)}
    synthesis_max_tokens: 2000
    synthesis_timeout_seconds: 120
    max_upload_size_mb: 100
    display_timezone: {json.dumps(rt.get("display_timezone") or "UTC")}
    vast_host: {json.dumps(rt.get("vast_host") or site["vms"]["host"])}
    tenant_name: {json.dumps(site["vms"]["tenant"])}
    jwt_secret: {json.dumps(rt["jwt_secret"])}
"""
    text = f"""apiVersion: v1
kind: Secret
metadata:
  name: video-backend-secret
  namespace: NAMESPACE
type: Opaque
stringData:
  config.yaml: |
{config}"""
    path = WORK / "k8s" / "backend-secret.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o600)


def kubectl_apply(env: dict, manifest: str) -> None:
    proc = subprocess.run(["kubectl", "apply", "-f", "-"], input=manifest, text=True, capture_output=True, env=env, timeout=60)
    if proc.returncode != 0:
        raise SystemExit(f"kubectl apply failed: {redact((proc.stderr or proc.stdout)[:300])}")


def ensure_tls_secret(site: dict, env: dict, tls: dict) -> None:
    ns = site["site"]["namespace"]
    say(f"tls secret {tls['secret']}", "1;36")
    created = subprocess.run(
        ["kubectl", "create", "namespace", ns, "--dry-run=client", "-o", "yaml"],
        text=True, capture_output=True, env=env, timeout=30,
    )
    if created.returncode != 0:
        raise SystemExit(f"namespace render failed: {redact(created.stderr[:200])}")
    kubectl_apply(env, created.stdout)
    secret = subprocess.run(
        [
            "kubectl", "create", "secret", "tls", tls["secret"],
            f"--cert={tls['cert']}", f"--key={tls['key']}",
            "-n", ns, "--dry-run=client", "-o", "yaml",
        ],
        text=True, capture_output=True, env=env, timeout=30,
    )
    if secret.returncode != 0:
        raise SystemExit(f"tls secret render failed: {redact(secret.stderr[:200])}")
    kubectl_apply(env, secret.stdout)
    say(f"  {tls['secret']} in {ns}", "32")


def deploy_k8s(site: dict, keys: dict) -> None:
    k8s = WORK / "k8s"
    if k8s.exists():
        shutil.rmtree(k8s)
    k8s.mkdir(parents=True)
    images = {
        "your.registry/vss-video-backend:v1": image_ref(site["images"]["kubernetes"]["backend"], None),
        "your.registry/vss-video-frontend:v1": image_ref(site["images"]["kubernetes"]["frontend"], None),
        "your.registry/vss-video-streaming:v1": image_ref(site["images"]["kubernetes"]["streaming"], None),
        "your.registry/vss-video-batch-sync:v1": image_ref(site["images"]["kubernetes"]["batch_sync"], None),
    }
    tls = https_config(site)
    for src in K8S_DIR.glob("*.yaml"):
        text = src.read_text()
        for old, new in images.items():
            text = text.replace(old, new)
        if tls and "kind: Ingress" in text:
            text = with_ingress_tls(text, tls["secret"])
        (k8s / src.name).write_text(text)
    shutil.copy(K8S_DIR / "QUICK_DEPLOY.sh", k8s / "QUICK_DEPLOY.sh")
    os.chmod(k8s / "QUICK_DEPLOY.sh", 0o755)
    write_backend_secret(site, keys)
    ns = site["site"]["namespace"]
    domain = site["site"]["ingress_domain"]
    env = kube_env(site)
    if tls:
        ensure_tls_secret(site, env, tls)
    say(f"kubectl apply via QUICK_DEPLOY.sh {ns} {domain}", "1;36")
    proc = subprocess.run(["bash", str(k8s / "QUICK_DEPLOY.sh"), ns, domain], env=env)
    if proc.returncode != 0:
        raise SystemExit(f"QUICK_DEPLOY.sh failed ({proc.returncode})")


K8S_OBJECTS = [
    ("deployment", "video-backend"),
    ("service", "video-backend-service"),
    ("ingress", "video-backend-ingress"),
    ("deployment", "video-frontend"),
    ("service", "video-frontend-service"),
    ("ingress", "video-frontend-ingress"),
    ("deployment", "video-stream-capture"),
    ("service", "video-stream-capture-service"),
    ("ingress", "video-stream-capture-ingress"),
    ("deployment", "video-batch-sync"),
    ("service", "video-batch-sync-service"),
    ("ingress", "video-batch-sync-ingress"),
    ("secret", "video-backend-secret"),
]


def cli_names(kind: str) -> set[str]:
    code, out = run(["vastde", kind, "list", "-o", "json"], timeout=60)
    if code != 0:
        say(f"vastde {kind} list failed: {redact(out)[:200]}", "1;31")
        return set()
    try:
        return {row.get("name") for row in rows_of(json_from_cli(out))}
    except Exception:
        return set()


def run_delete(cmd: list[str], timeout: int = 180) -> None:
    step(" ".join(cmd[:6]))
    proc = subprocess.run(cmd, input="y\n", text=True, capture_output=True, timeout=timeout)
    text = redact(ANSI.sub("", (proc.stdout or "") + (proc.stderr or ""))).rstrip()
    if text:
        print(text, flush=True)
    if proc.returncode != 0:
        raise SystemExit(f"delete failed: {' '.join(cmd[:4])}")


def cleanup_site(site: dict) -> None:
    """Remove the objects this site file owns. The namespace stays; it is attached to DataEngine."""
    ns = site["site"]["namespace"]
    env = kube_env(site)
    objects = list(K8S_OBJECTS)
    secret_name = (site.get("dataengine") or {}).get("secret")
    for extra in (secret_name, "vss-secret"):
        if extra and ("secret", extra) not in objects:
            objects.append(("secret", extra))
    tls = https_config(site)
    if tls and ("secret", tls["secret"]) not in objects:
        objects.append(("secret", tls["secret"]))
    say(f"kubernetes objects in {ns}", "1;33")
    for kind, name in objects:
        code, out = kubectl(env, "delete", kind, name, "-n", ns, "--ignore-not-found", timeout=60)
        if code == 0:
            say(f"  {kind}/{name}", "32")
        else:
            say(f"  {kind}/{name} {redact(out)[:160]}", "1;31")
    for fn in site.get("pipeline", {}).get("functions") or []:
        kubectl(env, "delete", "deployment", fn["name"], "-n", ns, "--ignore-not-found", timeout=60)
    kubectl(env, "label", "ns", ns, "zarf.dev/agent-", timeout=30)

    de = site["dataengine"]
    pipeline = de["pipeline"]
    if pipeline in cli_names("pipelines"):
        say(f"delete pipeline {pipeline}", "1;33")
        run_delete(["vastde", "pipelines", "delete", pipeline], timeout=300)
    else:
        say(f"pipeline {pipeline} absent", "32")

    function_names = cli_names("functions")
    for fn in site.get("pipeline", {}).get("functions") or []:
        name = fn["name"]
        if name not in function_names:
            say(f"function {name} absent", "32")
            continue
        say(f"delete function {name}", "1;33")
        run_delete(["vastde", "functions", "delete", name, "--force"])

    trigger_names = cli_names("triggers")
    for trig in site.get("pipeline", {}).get("triggers") or []:
        name = trig["name"]
        if name not in trigger_names:
            say(f"trigger {name} absent", "32")
            continue
        say(f"delete trigger {name}", "1;33")
        run_delete(["vastde", "triggers", "delete", name, "--force"])

    vms = Vms(site["vms"]["host"], site["vms"]["tenant"], site["vms"]["user"], site["vms"]["password"])
    code, data = vms.call("GET", f"/tenants/?name={site['vms']['tenant']}")
    tenant_rows = rows_of(data)
    tenant_id = tenant_rows[0].get("id") if code == 200 and tenant_rows else None
    broker = de["broker"]
    topic = de["topic"]
    code, out = run(["vastde", "topics", "list", "--database-name", broker, "-o", "json"], timeout=60)
    topic_names = []
    if code == 0:
        try:
            topic_names = [row.get("name") for row in rows_of(json_from_cli(out))]
        except Exception:
            topic_names = []
    if topic in topic_names:
        say(f"delete topic {topic}", "1;33")
        code, data = vms.call("DELETE", f"/topics/?database_name={broker}&name={topic}")
        if code not in (200, 202, 204):
            raise SystemExit(f"delete topic failed HTTP {code}: {redact(str(data)[:300])}")
    else:
        say(f"topic {topic} absent", "32")

    if tenant_id:
        code, listed = vms.call("GET", f"/views/?tenant_id={tenant_id}")
        by_bucket = {row.get("bucket"): row for row in rows_of(listed)}
        for bucket in (site.get("storage", {}).get("buckets") or {}).values():
            row = by_bucket.get(bucket)
            if not row:
                say(f"bucket {bucket} absent", "32")
                continue
            say(f"delete bucket {bucket}", "1;33")
            code, data = vms.call("DELETE", f"/views/{row['id']}/")
            if code not in (200, 202, 204):
                raise SystemExit(f"delete bucket {bucket} failed HTTP {code}: {redact(str(data)[:300])}")

    user_name = site["storage"]["user"]
    code, data = vms.call("GET", f"/users/?name={user_name}")
    users = [row for row in rows_of(data) if row.get("name") == user_name]
    if users:
        say(f"delete user {user_name}", "1;33")
        code, data = vms.call("DELETE", f"/users/{users[0]['id']}/")
        if code not in (200, 202, 204):
            raise SystemExit(f"delete user failed HTTP {code}: {redact(str(data)[:300])}")
    else:
        say(f"user {user_name} absent", "32")

    for name in ("storage-keys.yaml", "vss-secret.yaml", "pipeline.yaml"):
        path = WORK / name
        if path.exists():
            path.unlink()
            say(f"removed {name}", "32")
    k8s = WORK / "k8s"
    if k8s.exists():
        shutil.rmtree(k8s)
        say("removed deploy-work/k8s", "32")
    say("\ncleanup finished", "1;32")


def main() -> None:
    args = sys.argv[1:]
    check_only = "--check" in args
    cleanup_only = "--cleanup" in args
    no_cleanup = "--no-cleanup" in args
    paths = [a for a in args if not a.startswith("--")]
    if len(paths) != 1 or (check_only and cleanup_only):
        raise SystemExit("usage: deploy.sh <site.yaml> [--check|--cleanup|--no-cleanup]")
    site_path = Path(paths[0])
    if not site_path.is_file():
        raise SystemExit(f"site file not found: {site_path}")
    site = yaml.safe_load(site_path.read_text())
    if cleanup_only:
        say("\ncleanup\n", "1;33")
        cleanup_site(site)
        return
    checks, ctx = validate(site)
    failed = summary(checks)
    if failed:
        raise SystemExit(1)
    if check_only:
        return
    say("\nvalidation passed, starting deploy\n", "1;32")
    try:
        keys = deploy_storage(ctx)
        deploy_dataengine(site, keys)
        deploy_k8s(site, keys)
    except SystemExit as exc:
        if exc.code not in (0, None) and not no_cleanup:
            say("\ndeploy failed, cleaning up\n", "1;31")
            cleanup_site(site)
        raise
    except Exception:
        if not no_cleanup:
            say("\ndeploy failed, cleaning up\n", "1;31")
            cleanup_site(site)
        raise
    say("\ndeploy finished", "1;32")
    user_name = site["storage"]["user"]
    say(f"  {user_name} access key: {keys['access_key']}", "1;32")
    say(f"  {user_name} secret key: {keys['secret_key']}", "1;32")


if __name__ == "__main__":
    main()
