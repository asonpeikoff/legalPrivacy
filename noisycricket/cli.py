"""noisyCricket v2 CLI.

    cricket run [--profile P] [--once] [--dry-run] [--verbose]
    cricket doctor [--profile P]
    cricket relay gen --bandwidth 5Mbit --nickname mycricket [--bridge]
    cricket relay doctor
"""

from __future__ import annotations

import argparse
import sys

from .config import ConfigError, load_profile
from .relay import RelayConfigError, RelaySpec, generate_torrc, warn_if_exit_requested
from .scheduler import Runner, build_lanes


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
    wordlist = load_wordlist(
        next((e.wordlist for e in profile.engines.values()), None))
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
    # one-shot runs get a fixed clock hour so tests are deterministic
    runner = Runner(profile=profile, lanes=lanes, wordlist=wordlist,
                    clock=(lambda: 12.0) if args.once else None)
    if args.once:
        runner.run_once()
        return 0
    report = runner.run()
    for lane_name, engines in report.items():
        for eng, spent in engines.items():
            print(f"{lane_name}/{eng}: {spent} hits")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    problems = []
    if not any(tr.enabled for tr in profile.transports.values()):
        problems.append("no transport enabled")
    for name, eng in profile.engines.items():
        if eng.enabled and eng.budget_per_day == 0:
            problems.append(f"engine '{name}' enabled with zero budget")
    if profile.relay.enabled:
        if profile.relay.mode not in ("middle", "bridge"):
            problems.append("relay mode is not middle/bridge — refusing")
    total = sum(e.budget_per_day for e in profile.engines.values()
                if e.enabled)
    print(f"profile '{profile.name}': {total} hits/day planned, "
          f"{len(profile.engines)} engines, "
          f"relay={'yes' if profile.relay.enabled else 'no'}")
    for p in problems:
        print(f"  problem: {p}", file=sys.stderr)
    return 1 if problems else 0


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
    p_run.set_defaults(func=cmd_run)

    p_doc = sub.add_parser("doctor", help="validate a profile")
    p_doc.add_argument("--profile", help="profile YAML path")
    p_doc.set_defaults(func=cmd_doctor)

    p_relay = sub.add_parser("relay", help="community relay management")
    relay_sub = p_relay.add_subparsers(dest="relay_command", required=True)
    p_gen = relay_sub.add_parser("gen", help="generate a non-exit relay torrc")
    p_gen.add_argument("--bridge", action="store_true",
                       help="bridge instead of middle relay")
    p_gen.add_argument("--nickname", help="relay nickname (1-19 alnum)")
    p_gen.add_argument("--contact", help="contact email")
    p_gen.add_argument("--bandwidth", default="5Mbit")
    p_gen.add_argument("--out", default="torrc")
    p_gen.set_defaults(func=cmd_relay)
    relay_sub.add_parser("doctor").set_defaults(func=cmd_relay)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, RelayConfigError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
