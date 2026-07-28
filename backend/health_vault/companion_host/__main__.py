"""
HC-304B companion-only host process entry.

Usage (service wrapper loads env file into the process environment first):
  python -m backend.health_vault.companion_host

Secrets must already be present in the environment — never pass them as CLI args.
"""

from __future__ import annotations

import signal
import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv:
        # Refuse any CLI arguments to prevent secret leakage via process listings.
        print("companion_host_refuses_cli_arguments", file=sys.stderr)
        return 2

    from backend.health_vault.companion_host.activation import ActivationError
    from backend.health_vault.companion_host.app import build_activated_app
    from backend.health_vault.companion_host.logging_safe import configure_host_logging, log_event

    configure_host_logging()
    try:
        app, config, _store = build_activated_app()
    except ActivationError as exc:
        log_event("activation_failed", code=exc.code)
        print(f"activation_failed:{exc.code}", file=sys.stderr)
        return 1

    try:
        import uvicorn
    except Exception:
        print("uvicorn_required", file=sys.stderr)
        return 1

    should_stop = {"flag": False}

    def _handle_stop(signum, frame):  # noqa: ANN001
        should_stop["flag"] = True
        log_event("shutdown_signal", signum=int(signum))

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_stop)
        except (ValueError, OSError):
            pass

    log_event(
        "companion_host_starting",
        bind_host=config.bind_host,
        bind_port=config.bind_port,
        proxy_mode=config.trusted_proxy_mode,
        external_origin_configured=True,
    )
    # Bind loopback only — Tailscale Serve → local Caddy proxy → this process.
    uvicorn.run(
        app,
        host=config.bind_host,
        port=config.bind_port,
        log_level="warning",
    )
    log_event("companion_host_stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
