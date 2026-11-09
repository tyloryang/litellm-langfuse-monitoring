#!/usr/bin/env python3
"""Update the existing WSL stack in place, retaining containers and volumes.

Run with the reused LiteLLM venv's Python. Copies only this project's owned
observability files, backs up each changed runtime configuration and validates
Prometheus before reload. Does not print credentials or reset applications.
"""

import argparse
import datetime
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import urllib.request

import yaml
from dotenv import dotenv_values

SOURCE = Path(__file__).resolve().parents[1]


def run(args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed ({result.returncode}): {result.stderr[-1600:]}")
    return result.stdout


def inspect(name):
    return json.loads(run(["docker", "inspect", name]))[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, default=Path.home() / "litellm-langfuse-monitoring")
    parser.add_argument("--prometheus", default="llm-observability-prometheus")
    parser.add_argument("--grafana", default="llm-observability-grafana")
    parser.add_argument("--nginx", default="llm-gateway-nginx")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    runtime = args.runtime_root.resolve()
    env = dotenv_values(runtime / ".env")
    if not env.get("STRATA_API_KEY"):
        raise RuntimeError("Existing runtime .env is missing STRATA_API_KEY")
    prom, grafana = inspect(args.prometheus), inspect(args.grafana)
    pmounts = {m["Destination"]: Path(m["Source"]) for m in prom["Mounts"] if m["Type"] == "bind"}
    gmounts = {m["Destination"]: Path(m["Source"]) for m in grafana["Mounts"] if m["Type"] == "bind"}
    pconfig, rules = pmounts["/etc/prometheus/prometheus.yml"], pmounts["/etc/prometheus/rules"]
    provisioning = gmounts["/etc/grafana/provisioning"]
    provider = next((provisioning / "dashboards").glob("*.yaml"))
    provider_data = yaml.safe_load(provider.read_text())
    dashboard_dir = provisioning / Path(provider_data["providers"][0]["options"]["path"]).relative_to("/etc/grafana/provisioning")
    networks = prom["NetworkSettings"]["Networks"]
    gateway = next(v["Gateway"] for v in networks.values() if v.get("Gateway"))
    nginx = inspect(args.nginx)
    proxy_addresses = [value["IPAddress"] + "/32" for name, value in nginx["NetworkSettings"]["Networks"].items()
                       if name in networks and value.get("IPAddress")]
    if not proxy_addresses:
        raise RuntimeError("Cannot discover trusted Nginx address on the existing observability network")
    venv_python = Path.home() / "llm-observability/.venv/bin/python"
    if not venv_python.exists():
        raise RuntimeError("Expected reused LiteLLM virtualenv was not found")
    print(json.dumps({"runtime": str(runtime), "prometheus_config": str(pconfig), "rules": str(rules),
                      "dashboard_dir": str(dashboard_dir), "host_gateway": gateway,
                      "reuse": [args.prometheus, args.grafana, "LiteLLM venv", "Langfuse and all data volumes"]}, ensure_ascii=False))
    if args.dry_run:
        return
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = runtime / "backups" / ("observability-" + stamp)
    backup.mkdir(parents=True, mode=0o700)
    saved = []

    def save(path):
        if path.exists() and path.is_file():
            target = backup / (str(len(saved)) + "-" + path.name)
            shutil.copy2(path, target)
            target.chmod(0o600)
            saved.append({"original": str(path), "backup": str(target)})
            (backup / "manifest.json").write_text(json.dumps(saved, indent=2), encoding="utf-8")

    def put(path, text, mode=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        save(path)
        # Preserve the inode of bind-mounted files; atomic replacement alone
        # leaves a Docker file bind mount attached to the previous inode.
        path.write_text(text, encoding="utf-8")
        if mode is not None:
            path.chmod(mode)

    for relative in ["litellm/observability_callback.py", "exporters/strata_extra.py", "examples/traced_tool_agent.py"]:
        source = SOURCE / relative
        if source.exists():
            put(runtime / relative, source.read_text(encoding="utf-8"))
    config_path = runtime / "litellm/config.local.yaml"
    # Exercise LiteLLM's actual file loader before changing the running config.
    # Its import behavior differs from a normal Python package import.
    from litellm.proxy.types_utils.utils import get_instance_fn
    get_instance_fn(value="observability_callback.observability", config_file_path=str(config_path))
    runtime_env = runtime / ".env"
    env_text = runtime_env.read_text()
    usage_db = runtime / "data/source-usage/usage.sqlite3"
    if usage_db.exists():
        target = backup / "source-usage.sqlite3"
        with sqlite3.connect(usage_db) as source_db, sqlite3.connect(target) as target_db:
            source_db.backup(target_db)
        target.chmod(0o600)
    for key, value in {"LITELLM_OTEL_V2": "false", "LITELLM_LOCAL_MODEL_COST_MAP": "True",
                       "FORWARDED_ALLOW_IPS": "''", "GATEWAY_TRUSTED_PROXY_CIDRS": ",".join(proxy_addresses),
                       "GATEWAY_SOURCE_USAGE_DB": str(usage_db)}.items():
        line = key + "=" + value
        env_text = re.sub(r"(?m)^" + key + r"=.*$", line, env_text) if re.search(r"(?m)^" + key + "=", env_text) else env_text.rstrip() + "\n" + line + "\n"
    put(runtime_env, env_text, 0o600)
    config = yaml.safe_load(config_path.read_text())
    config.setdefault("general_settings", {})["use_x_forwarded_for"] = False
    settings = config.setdefault("litellm_settings", {})
    callbacks = [c for c in settings.get("callbacks", []) if c not in {"langfuse", "langfuse_otel", "observability_callback.observability"}]
    settings["callbacks"] = callbacks + ["observability_callback.observability"]
    put(config_path, yaml.safe_dump(config, sort_keys=False, allow_unicode=True), 0o600)
    # This reused host gateway deliberately has no LiteLLM database. LiteLLM's
    # DEV loader can otherwise re-read the shared .env after `unset` and try
    # migrating Langfuse's container-only DATABASE_URL on every restart.
    gateway_runner = runtime / "litellm/run-local.sh"
    put(gateway_runner, '#!/usr/bin/env bash\nset -euo pipefail\ncd "' + str(runtime) + '"\n'
        'set -a\nsource ./.env\nset +a\n'
        'unset DATABASE_URL DIRECT_URL DATABASE_URL_READ_REPLICA\n'
        'export LITELLM_MODE=PRODUCTION PYTHON_DOTENV_DISABLED=1\n'
        'exec "' + str(venv_python.parent / "litellm") + '" --config ./litellm/config.local.yaml --host 0.0.0.0 --port 4000\n', 0o700)
    rules.mkdir(parents=True, exist_ok=True)
    for filename in ["recording-rules.yml"]:
        put(rules / ("llm-" + filename), (SOURCE / "prometheus" / filename).read_text())
    # Container uid 65534 can read this narrow credential file. The rule glob
    # uses *.yml so the token is never parsed as a rule file.
    token = rules / ".strata-api-key"
    put(token, env["STRATA_API_KEY"], 0o640)
    # The existing container mounts rules read-only. A short-lived helper uses
    # its already-present image to set only the credential file's group. Keep
    # the host user as owner so repeat deployments can rotate the credential.
    run(["docker", "run", "--rm", "--pull=never", "--network=none", "--user=0",
         "--entrypoint=/bin/chgrp", "-v", str(rules) + ":/rules", prom["Config"]["Image"],
         "65534", "/rules/.strata-api-key"])
    pdata = yaml.safe_load(pconfig.read_text())
    jobs = pdata.setdefault("scrape_configs", [])
    for name, port in [("strata", 8080), ("strata-extra", 9402)]:
        if any(j.get("job_name") == name for j in jobs):
            jobs[:] = [j for j in jobs if j.get("job_name") != name]
        job = {"job_name": name, "scrape_interval": "5s", "static_configs": [
            {"targets": [f"{gateway}:{port}"], "labels": {"engine": "strata", "deployment": "wsl-rtx3080"}}]}
        if name == "strata":
            job.update(metrics_path="/metrics", params={"format": ["prometheus"]},
                       authorization={"type": "Bearer", "credentials_file": "/etc/prometheus/rules/.strata-api-key"})
        jobs.append(job)
    files = pdata.setdefault("rule_files", [])
    # Keep the existing alert filename in place when present, avoiding duplicate
    # alert definitions under two rule files.
    for rule in ("/etc/prometheus/rules/llm-recording-rules.yml",):
        if rule not in files:
            files.append(rule)
    existing_alert = rules / "llm-alerts.yml"
    put(existing_alert, (SOURCE / "prometheus/alerts.yml").read_text())
    if "/etc/prometheus/rules/llm-alerts.yml" not in files:
        files.append("/etc/prometheus/rules/llm-alerts.yml")
    put(pconfig, yaml.safe_dump(pdata, sort_keys=False, allow_unicode=True))
    put(dashboard_dir / "litellm-gpu-overview.json", (SOURCE / "grafana/dashboards/litellm-gpu-overview.json").read_text(encoding="utf-8"))
    # Provisioning watches this directory; the existing datasource is selected
    # through a real datasource variable rather than an unresolved import UID.
    exporter_runner = runtime / "exporters/run-local.sh"
    put(exporter_runner, '#!/usr/bin/env bash\nset -euo pipefail\ncd "' + str(runtime) + '"\nset -a\nsource ./.env\nset +a\nexport STRATA_EXPORTER_HOST="' + gateway + '"\nexec "' + str(venv_python) + '" exporters/strata_extra.py\n', 0o700)
    unit = Path.home() / ".config/systemd/user/strata-metrics-extra.service"
    put(unit, "[Unit]\nDescription=Strata Monitor supplemental Prometheus exporter\nAfter=network.target\n[Service]\nExecStart=" + str(exporter_runner) + "\nRestart=on-failure\nRestartSec=5\n[Install]\nWantedBy=default.target\n")
    (backup / "manifest.json").write_text(json.dumps(saved, indent=2), encoding="utf-8")
    print(run(["docker", "exec", args.prometheus, "promtool", "check", "config", "/etc/prometheus/prometheus.yml"]))
    run(["systemctl", "--user", "daemon-reload"])
    run(["systemctl", "--user", "enable", "--now", "strata-metrics-extra.service"])
    run(["systemctl", "--user", "restart", "strata-metrics-extra.service"])
    # No containers or databases are restarted. Only the stateless gateway
    # process must reload its callback; preserve all inference server state.
    run(["systemctl", "--user", "restart", "litellm-gateway.service"])
    healthy = False
    for attempt in range(180):
        try:
            with urllib.request.urlopen("http://127.0.0.1:4000/health/liveliness", timeout=1) as response:
                healthy = response.status == 200
            if healthy:
                break
        except OSError:
            time.sleep(.5)
    if not healthy:
        original = next((entry for entry in saved if entry["original"] == str(config_path)), None)
        if original:
            config_path.write_bytes(Path(original["backup"]).read_bytes())
            run(["systemctl", "--user", "restart", "litellm-gateway.service"])
        raise RuntimeError("Gateway did not become ready; its previous config was restored. Inspect the service journal.")
    run(["docker", "kill", "--signal=HUP", args.prometheus])
    print("Applied. Backup manifest: " + str(backup / "manifest.json"))


if __name__ == "__main__":
    main()
