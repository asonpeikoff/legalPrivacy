# Running legalPrivacy in Docker (home server, NAS, Raspberry Pi)

The image includes Chromium (real-browser noise) and Tor (optional .onion noise).
Settings live in a Docker volume, so they survive updates.

```sh
docker compose up -d                      # build and start
docker compose logs legalprivacy          # shows the dashboard link with its token
```

Open the printed link (`http://127.0.0.1:8765/?t=…`) on the same machine.

## Things to know

- **The dashboard stays private.** It is published to `127.0.0.1` on the host only. To reach it
  from another computer, use an SSH tunnel (`ssh -L 8765:127.0.0.1:8765 your-server`), not a
  public port. Keep both sides of the port mapping the same (8765:8765): the dashboard only
  answers to its own address.
- **Set your time zone** (`TZ` in `docker-compose.yml`, e.g. `America/New_York`). Noise follows
  local hours, active hours and the day/night curve.
- **DNS.** Inside a container, "your normal DNS" goes through Docker's resolver, which forwards
  to the host's. On Linux, `network_mode: host` makes lookups and traffic look exactly like the
  host's own (and lets "Your own activity" see the host's traffic). Remove the `ports:` section
  if you use it.
- **Chromium's own sandbox is off** inside the container (`LEGALPRIVACY_NO_SANDBOX=1`): the container
  is the sandbox. It runs as an unprivileged user.
- **Memory.** Chromium needs `shm_size: 1gb` (set in the compose file) and about 500 MB of RAM.

## Raspberry Pi

Use a 64-bit OS (Raspberry Pi OS 64-bit or Ubuntu) and Docker. The same `docker compose up -d`
builds natively on the Pi (Python, Chromium and Tor are all available for arm64). A Pi 4 or 5
with 2 GB+ is comfortable; on a Pi with less, set the browser to "Off" in Settings → Realism
and rely on plain HTTP and DNS noise.

## Not included

- **Home Assistant add-on.** Home Assistant OS only runs add-ons built on its own base images,
  which this sandbox couldn't build or test. Running the compose file on any Docker host on the
  same network covers the same need.
