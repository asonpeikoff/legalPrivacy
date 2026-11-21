# MAC address spoofing

Part of legalPrivacy, which is named in homage to the blog
[*Legalizing Privacy*](https://legalizingprivacy.substack.com) by Amy Peikoff
([about](https://legalizingprivacy.substack.com/about), [archive](https://legalizingprivacy.substack.com/archive)).

`legalprivacy mac` changes the hardware (MAC) address your network interfaces present, and restores the permanent one.
Linux only. Standard tools only: `ip`, `nmcli` (optional) and `ethtool`. Source: `legalprivacy/macspoof.py`.

## Commands

| Command | What it does |
|---|---|
| `mac status [--json]` | Current vs permanent MAC per interface, default-route flag, rotation state. No root needed. |
| `mac randomize IFACE\|--all [--mode random\|stable] [--persist] [--force]` | New address. `random`: new each connection. `stable`: same per network (NetworkManager). |
| `mac set IFACE MAC [--persist] [--force]` | Fixed address (unicast, not all-zero). |
| `mac restore IFACE\|--all [--force]` | Back to the permanent address; verified afterwards. |
| `mac rotate --enable --interval N [--include-active]` / `--disable` | Opt-in timed rotation, N = 15 to 1440 minutes. |

Exit codes: `0` ok, `1` error, `2` bad arguments, `3` permission denied. `--json` prints `{"ok": ..., "results": [...]}`
(or `{"ok": false, "error": ..., "exit_code": n}`).

## How it works

- **Generated addresses:** six random octets with the first set to `(b & 0xFE) | 0x02`, so they are unicast and locally administered.
- **NetworkManager (preferred):** `nmcli connection modify --temporary CONN wifi.cloned-mac-address VALUE` (or `ethernet.`), then
  `nmcli connection up`. `--temporary` keeps it out of the saved profile, so a reboot or NetworkManager restart reverts it;
  `--persist` writes it to the profile. The original profile value is saved and put back by `restore`.
- **Fallback:** `ip link set dev IF down`, `address MAC`, `up` (the link is always brought back up, even on failure). Used when
  NetworkManager is absent or the interface has no active connection. It cannot persist across reboot.
- **Restore:** the permanent MAC (`ethtool -P`) is recorded in `mac_state.json` (mode 0600, in the app directory) before the
  first change and never overwritten. If it cannot be determined, nothing is changed.

## Safety

- Needs root for anything that changes an address.
- The interface carrying the default route is skipped (an error if you name it) unless `--force` / `--include-active`, because
  changing it drops your connection.
- Bridges, bridge and bond members, loopback and virtual interfaces (veth, tun, wg, docker...) are never touched, even with `--force`.
- Rotation is off by default. It runs from a systemd timer (or `/etc/cron.d` without systemd) that wakes every 15 minutes and
  rotates when the interval is due. It never rotates the default-route interface while a VPN interface (tun/tap/wg/ppp/tailscale...)
  is up or a Tor SOCKS port (9050/9150) is listening. `--disable` removes the timer.

## What to expect

- A new MAC means a **new DHCP lease and possibly a new IP address**; existing connections drop.
- **Captive portals** (hotels, cafes) usually ask you to sign in again; `stable` mode avoids repeated prompts on the same network.
- **MAC-filtered networks** may refuse an unknown address; `restore` it or use a permitted one.
- **Other identifiers are separate leaks and are not changed:** your hostname, the DHCP client identifier, Wi-Fi probe/scan
  behaviour, and anything at higher layers (browser fingerprint, accounts). Set a generic hostname and configure your DHCP
  client not to send one if this matters to you.
- **Legal:** changing the address of your own device is generally lawful, but it may breach a network's terms of use. You are
  responsible for where you use it.

## Not verified on real hardware

The tests mock `ip`, `nmcli`, `ethtool` and sysfs. Real NIC drivers that refuse address changes, NetworkManager version
differences, the systemd timer install and the Tor/VPN detection on a live machine are untested.
