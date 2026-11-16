"""noisyCricket v2 CLI.

    cricket run [--profile P] [--once] [--dry-run] [--verbose]
    cricket doctor [--profile P]
    cricket relay gen --bandwidth 5Mbit --nickname mycricket [--bridge]
    cricket relay doctor
"""

from __future__ import annotations

import argparse
import sys
import time

from .config import ConfigError, load_profile
from .relay import RelayConfigError, RelaySpec, generate_torrc, warn_if_exit_requested
from .scheduler import MaxFailuresExceeded, Runner, build_lanes


def _init_random(seed_env: str = "CRICKET_SEED") -> int:
    """Deterministic seed for reproducible test runs, random otherwise."""
    import os
    import random
    env = os.environ.get(seed_env)
    return random.Random(int(env)) if env else random.Random()


def cmd_run(args: argparse.Namespace) -> int:
    from .engines import load_wordlist
    profile = load_profile(args.profile)
    rng = _init_random()
    active_wordlist = next((e.wordlist for e in profile.engines.values() if e.enabled and e.wordlist), None)
    wordlist = load_wordlist(active_wordlist)
    lanes = build_lanes(profile, wordlist, rng)
    if not lanes:
        print("no active lanes: enable a transport in the profile", file=sys.stderr)
        return 2
    if args.dry_run:
        for lane in lanes:
            engines = ", ".join(f"{e} ({lane.budgets[e].remaining})"
                                for e in lane.engines)
            print(f"lane {lane.name:8s} transport={type(lane.transport).__name__:18s} "
                  f"engines: {engines}")
        return 0
    if args.reconnect_tor:
        for lane in lanes:
            if hasattr(lane.transport, "reconnect"):
                ok = lane.transport.reconnect()
                if args.verbose:
                    print(f"Tor circuit reset requested: {'OK' if ok else 'failed (control port offline)'}")
    # one-shot runs get a fixed clock hour so tests are deterministic
    runner = Runner(
        profile=profile,
        lanes=lanes,
        wordlist=wordlist,
        clock=time.time,
        local_hour_fn=(lambda: 12.0) if args.once else None,
        verbose=args.verbose,
    )
    if args.once:
        runner.run_once(wait=False)
        return 0
    report = runner.run()
    for lane_name, engines in report.items():
        for eng, spent in engines.items():
            print(f"{lane_name}/{eng}: {spent} hits")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    import os
    profile = load_profile(args.profile)
    problems = []
    warnings = []

    total_hits = sum(e.budget_per_day for e in profile.engines.values() if e.enabled)
    print(f"Doctor diagnostics for profile '{profile.name}':")
    print(f"  [+] Schedule: {total_hits} hits/day planned across {len(profile.engines)} engines.")

    # 1. Wordlist check
    active_wordlist = next((e.wordlist for e in profile.engines.values() if e.enabled and e.wordlist), None)
    if active_wordlist and not os.path.exists(active_wordlist):
        problems.append(f"wordlist file not found: {active_wordlist}")
    elif active_wordlist:
        try:
            with open(active_wordlist, encoding="utf-8", errors="ignore") as fh:
                w_count = sum(1 for line in fh if line.strip())
            print(f"  [+] Wordlist: '{active_wordlist}' verified ({w_count:,} words).")
        except OSError:
            problems.append(f"could not read wordlist: {active_wordlist}")

    # 2. Clearnet check
    clearnet_cfg = profile.transports.get("clearnet")
    if clearnet_cfg and clearnet_cfg.enabled:
        proxy_info = clearnet_cfg.proxy or "direct connection"
        print(f"  [+] Clearnet lane: enabled ({proxy_info})")

    # 3. Tor check with live port probing
    tor_cfg = profile.transports.get("tor")
    if tor_cfg and tor_cfg.enabled:
        from .transports import TorTransport
        tor = TorTransport(socks=tor_cfg.socks, control=tor_cfg.control,
                           identities_per_circuit=tor_cfg.identities_per_circuit,
                           control_password=tor_cfg.control_password)
        tor_status = tor.check_status()
        if tor_status["socks_ok"]:
            print(f"  [+] Tor SOCKS daemon ({tor_status['socks_host']}:{tor_status['socks_port']}): CONNECTED")
        else:
            warnings.append(f"Tor SOCKS daemon offline at {tor_status['socks_host']}:{tor_status['socks_port']}")
            print(f"  [-] Tor SOCKS daemon ({tor_status['socks_host']}:{tor_status['socks_port']}): OFFLINE")
            print("      Action: Start Tor ('sudo systemctl start tor') or run Tor with bridges ('cricket relay bridges')")

        if tor_cfg.control:
            if tor_status["control_ok"]:
                ver = f" (Tor {tor_status['version']})" if tor_status.get("version") else ""
                boot = f" [{tor_status['bootstrap']}]" if tor_status.get("bootstrap") else ""
                print(f"  [+] Tor Control port ({tor_cfg.control}): AUTHENTICATED{ver}{boot}")
            else:
                warnings.append(f"Tor Control port unreachable or unauthenticated at {tor_cfg.control}")
                print(f"  [-] Tor Control port ({tor_cfg.control}): UNAVAILABLE (automatic NEWNYM rotation disabled)")

    if not any(tr.enabled for tr in profile.transports.values()):
        problems.append("no transport enabled in profile")

    for name, eng in profile.engines.items():
        if eng.enabled and eng.budget_per_day == 0:
            problems.append(f"engine '{name}' enabled with zero budget")

    if profile.relay.enabled:
        if profile.relay.mode not in ("middle", "bridge"):
            problems.append("relay mode is not middle/bridge — refusing")
        else:
            print(f"  [+] Relay contribution: enabled ({profile.relay.mode} relay '{profile.relay.nickname}')")

    if warnings:
        for w in warnings:
            print(f"  [warning] {w}", file=sys.stderr)
    if problems:
        for p in problems:
            print(f"  [problem] {p}", file=sys.stderr)
        return 1
    return 0


def cmd_relay(args: argparse.Namespace) -> int:
    if args.relay_command == "gen":
        spec = RelaySpec(mode="bridge" if args.bridge else "middle",
                         nickname=args.nickname or "noisycricket",
                         contact=args.contact,
                         bandwidth_rate=args.bandwidth)
        torrc = generate_torrc(spec)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(torrc)
        warnings = warn_if_exit_requested(torrc)
        for w in warnings:
            print(w, file=sys.stderr)
        print(f"wrote {args.out}: {spec.mode} relay '{spec.nickname}', "
              f"rate {spec.bandwidth_rate}, exit policy reject-all")
        return 0
    if args.relay_command == "bridges":
        import os
        from pathlib import Path
        bridges_path = Path(__file__).resolve().parents[1] / "data" / "bridges" / "bridges.example.txt"
        if bridges_path.exists():
            with open(bridges_path, encoding="utf-8") as fh:
                print(fh.read())
        else:
            from .relay import DEFAULT_SNOWFLAKE_BRIDGES
            print("# Collected Tor Snowflake Bridges:\n")
            for b in DEFAULT_SNOWFLAKE_BRIDGES:
                print(b)
        return 0
    if args.relay_command == "gen-client":
        from .relay import generate_client_torrc
        torrc = generate_client_torrc(bridge_type=args.type)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(torrc)
        print(f"wrote client torrc with {args.type} bridges to {args.out}")
        print(f"To start Tor with this configuration: tor -f {args.out}")
        return 0
    if args.relay_command == "doctor":
        print("relay doctor: verify your relay is reachable from the network via "
              "https://metrics.torproject.org/ after 8h, and that it is NOT listed "
              "as an exit.")
        return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cricket", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run a noise schedule")
    p_run.add_argument("--profile", help="profile YAML path")
    p_run.add_argument("--once", action="store_true", help="run a single job")
    p_run.add_argument("--dry-run", action="store_true",
                       help="show lanes and budgets without traffic")
    p_run.add_argument("--verbose", action="store_true")
    p_run.add_argument("--reconnect-tor", action="store_true",
                       help="signal NEWNYM to rotate Tor circuits before running")
    p_run.set_defaults(func=cmd_run)

    p_doc = sub.add_parser("doctor", help="validate a profile and probe transport health")
    p_doc.add_argument("--profile", help="profile YAML path")
    p_doc.set_defaults(func=cmd_doctor)

    p_relay = sub.add_parser("relay", help="community relay and bridge management")
    relay_sub = p_relay.add_subparsers(dest="relay_command", required=True)
    p_gen = relay_sub.add_parser("gen", help="generate a non-exit relay torrc")
    p_gen.add_argument("--bridge", action="store_true",
                       help="bridge instead of middle relay")
    p_gen.add_argument("--nickname", help="relay nickname (1-19 alnum)")
    p_gen.add_argument("--contact", help="contact email")
    p_gen.add_argument("--bandwidth", default="5Mbit")
    p_gen.add_argument("--out", default="torrc")
    p_gen.set_defaults(func=cmd_relay)

    p_gen_client = relay_sub.add_parser("gen-client", help="generate client torrc with bridges")
    p_gen_client.add_argument("--type", choices=["snowflake", "obfs4"], default="snowflake",
                              help="bridge technology (snowflake or obfs4)")
    p_gen_client.add_argument("--out", default="torrc.client", help="destination torrc path")
    p_gen_client.set_defaults(func=cmd_relay)

    p_bridges = relay_sub.add_parser("bridges", help="display collected Tor bridges and usage instructions")
    p_bridges.set_defaults(func=cmd_relay)

    relay_sub.add_parser("doctor").set_defaults(func=cmd_relay)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, RelayConfigError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except MaxFailuresExceeded as exc:
        print(f"execution halted: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
