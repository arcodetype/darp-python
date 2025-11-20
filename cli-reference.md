# darp CLI Reference

`darp` (<span style="color:#d670d6">d</span>irectories <span style="color:#d670d6">a</span>uto-r<span style="color:#d670d6">r</span>everse <span style="color:#d670d6">p</span>roxied) is a CLI that automatically reverse-proxies local project folders into nice `.test` domains (e.g. `hello-world.projects.test`) using Docker or Podman, nginx, and dnsmasq.

This document describes all available commands and how they interact.

---

## Core Concepts

### Config & Data Locations

By default, darp stores its configuration under:

- `DARP_ROOT` (env var, default: `~/.darp`)
  - `config.json` — main configuration (domains, environments, engine, etc.)
  - `portmap.json` — generated port mappings `{domain: {project: port}}`
  - `dnsmasq.d/` — dnsmasq configuration snippets (e.g. `test.conf`)
  - `vhost_container.conf` — generated nginx virtual host config
  - `hosts_container` — hosts file used inside containers
  - `nginx.conf` — nginx base configuration for the reverse proxy

System files darp touches:

- `/etc/resolver/test` — macOS resolver pointing `.test` domains to `127.0.0.1`
- `/etc/hosts` — optionally updated with Darp domains (if `urls_in_hosts` is enabled)

### Engines & Environment Variables

- `ENGINE` — chosen via `darp set engine podman|docker`
- `CONTAINER_BIN` — derived from ENGINE (`docker` or `podman`)
- `PODMAN_MACHINE` — which Podman machine to target (env var, set via `darp set PODMAN_MACHINE`)
- `DARP_ROOT` — override root config directory (set via `darp set DARP_ROOT`)

Many commands require an engine to be set and available; they’ll fail fast with a helpful message if Docker/Podman is not running.

---

## Command Overview

Top-level commands:

- `darp init` — One-time initialization (resolver, dnsmasq, nginx config)
- `darp deploy` — Discover projects, assign ports, generate nginx config, and restart proxy
- `darp shell` — Start a dev container and drop into an interactive shell
- `darp serve` — Start a dev container and run an environment’s `serve_command`
- `darp set ...` — Configure engine, DARP_ROOT, PODMAN_MACHINE, urls_in_hosts, etc.
- `darp add ...` — Add domains, environments, volumes, and port mappings
- `darp rm ...` — Remove domains, environments, volumes, port mappings, and env settings
- `darp urls` — List configured project URLs and their ports

Each is detailed below.

---

## `darp init`

**Purpose:** One-time, sudo-level setup so `.test` domains resolve locally and dnsmasq/nginx can run properly (especially for Podman).

**What it does:**

1. Ensures `/etc/resolver` exists.
2. Writes `/etc/resolver/test` with:
   ```txt
   nameserver 127.0.0.1
   ```
3. Ensures `DNSMASQ_DIR` exists (`$DARP_ROOT/dnsmasq.d`).
4. Copies `/usr/local/opt/darp/nginx.conf` into `$DARP_ROOT`.
5. Writes `$DARP_ROOT/dnsmasq.d/test.conf`:
   ```txt
   address=/.test/127.0.0.1
   ```
6. **Podman only:**
   - Detects Podman machine rootful/rootless.
   - If rootless, SSHes into the Podman machine and sets:
     ```txt
     net.ipv4.ip_unprivileged_port_start=53
     ```
     in `/etc/sysctl.conf`, then runs `sysctl --system`.

**Typical usage:**

```sh
darp init
```

Required before `deploy`, `shell`, `serve`, and `urls` are fully usable.

---

## `darp deploy`

**Purpose:** Scan configured domains, detect projects, assign ports, and regenerate the reverse proxy and dnsmasq data.

**What it does:**

1. Loads `config.json` and ensures at least one `domain` is configured.
2. For each domain:
   - Looks at the domain’s `location` (e.g. `~/projects`).
   - Lists subdirectories (`drwxr-xr-x` entries).
   - Assigns ports starting at `50100` (per domain/project).
   - Creates hostnames like:  
     `folder.domain.test` → mapped to `0.0.0.0` inside containers.
3. Writes:
   - `hosts_container` — List of `0.0.0.0 domain.test` lines.
   - `portmap.json` — `{ domain: { project: port } }`.
   - `vhost_container.conf` — nginx `server {}` blocks proxying `host_gateway:port` to each project.
4. Restarts the nginx reverse proxy container (`darp-reverse-proxy`) and stops any `darp_*` containers.
5. If `urls_in_hosts` is enabled, updates `/etc/hosts` with a managed Darp block mapping domains to `127.0.0.1`.

**Typical usage:**

```sh
darp deploy
```

Run this whenever:

- You add or remove project directories under a domain.
- You change domain configuration.
- You want `/etc/hosts` re-synced (when `urls_in_hosts` is true).

---

## `darp shell`

**Purpose:** Start a dev container for the current project and drop into an interactive shell with reverse proxy and optional volumes.

**Usage:**

```sh
darp shell [-e ENVIRONMENT] <container_image>
```

**Behavior:**

- Requires an engine (`podman` or `docker`) and an existing `domain` + `deploy`.
- Determines:
  - `current_directory` (`/path/to/projects/hello-world`)
  - `parent_directory` (`/path/to/projects`)
  - `parent_directory_name` (e.g. `projects`)
  - `current_directory_name` (e.g. `hello-world`)
- Ensures `parent_directory_name` is configured as a domain.
- Builds `container_name` as:  
  `darp_projects_hello-world`.
- Assembles `podman run` / `docker run` command:
  - `-v <current_directory>:/app`
  - `-v hosts_container:/etc/hosts`
  - `-v nginx.conf:/etc/nginx/nginx.conf`
  - `-v vhost_container.conf:/etc/nginx/http.d/vhost_docker.conf`
  - Adds extra volumes from the environment’s `volumes`, resolving `{pwd}` tokens.
  - Adds any host port mappings from `domain.services[project].host_portmappings`.
  - Maps the assigned reverse proxy port → `8000` in container.
- Resolves actual image name based on environment’s `image_repository` (if set).
- Inside the container:
  - If `nginx` exists, starts it.
  - Prints a reminder:
    ```txt
    To leave this shell and stop the container, type: exit
    ```
  - Drops you into `sh` in `/app`.

**Auto-restart:**

- Uses `run_container_interactive(..., restart_on={137})`, so it will auto-restart on exit code `137` (e.g. OOM/kill), but not on `Ctrl+C`.

---

## `darp serve`

**Purpose:** Similar to `shell`, but meant for running your app’s serve command (e.g. `air`, `npm run dev`) defined per environment.

**Usage:**

```sh
darp serve -e ENVIRONMENT <container_image>
```

**Behavior:**

- Requires:
  - Engine set and ready.
  - Environment exists in `config.json`.
  - Environment has `serve_command` defined (`darp set serve_command`).
  - Domain + `deploy` already run.
- Assembles a container much like `darp shell`:
  - Mounts `/app`, hosts_container, nginx configs, and environment volumes.
  - Applies port mappings, including reverse proxy port → `8000`.
- Inside the container:
  - Starts `nginx` if present.
  - `cd /app; <serve_command>` (e.g. `air`).

**Auto-restart:**

- Uses `run_container_interactive(..., restart_on={0, 2})`:
  - Auto-restarts on exit codes `0` or `2` (helpful if the command exits cleanly but should be restarted).
  - Stops on `Ctrl+C` like other commands.

---

## `darp set ...`

Group of commands to configure core settings.

### `darp set DARP_ROOT`

```sh
darp set DARP_ROOT <NEW_DARP_ROOT> [-z PATH_TO_ZSHRC]
```

- Updates or appends `export DARP_ROOT="<NEW_DARP_ROOT>"` in the given `.zshrc` (default: `~/.zshrc`).
- Removes any previous `export DARP_ROOT=...` lines first.
- Prints a reminder to restart the shell or `source` the config.

---

### `darp set PODMAN_MACHINE`

```sh
darp set PODMAN_MACHINE <machine_name> [-z PATH_TO_ZSHRC]
```

- Writes `export PODMAN_MACHINE="<machine_name>"` into `.zshrc` (default: `~/.zshrc`).
- Used to choose which Podman machine darp should target.
- Reminds you to restart shell or `source` the config.

---

### `darp set engine`

```sh
darp set engine <podman|docker>
```

- Valid values: `podman`, `docker`.
- Updates `config.json` under `"engine"`.
- On next run, darp will use the chosen engine and check its readiness.

---

### `darp set image_repository`

```sh
darp set image_repository <environment> <image_repository>
```

- Sets a base image repository for an environment, e.g.:
  - `"git.company.org:4567/dev/team/image"`
- When running `shell`/`serve`, `container_image` is resolved as:
  - `<image_repository>:<container_image>`

---

### `darp set serve_command`

```sh
darp set serve_command <environment> <serve_command>
```

- Defines the command darp should run inside containers for `darp serve`.
- Example:
  ```sh
  darp set serve_command go 'air'
  ```

---

### `darp set urls_in_hosts`

```sh
darp set urls_in_hosts <TRUE|FALSE>
```

- Controls whether `darp deploy` should mirror Darp URLs into `/etc/hosts` as `127.0.0.1`.
- Accepts typical boolean-ish strings:
  - `true/false`, `yes/no`, `1/0`, `y/n`, `on/off`.
- Stored in `config.json` under `"urls_in_hosts"`.

---

## `darp add ...`

Group of commands to add domains, environments, portmaps, and volumes.

### `darp add domain`

```sh
darp add domain <location>
```

- `location` is a directory path (e.g. `~/projects`).
- Domain name is derived from `basename(location)` (e.g. `projects`).
- Fails if a domain with that name already exists.

Resulting config snippet:

```json
"domains": {
  "projects": {
    "location": "/Users/you/projects"
  }
}
```

---

### `darp add environment`

```sh
darp add environment <name>
```

- Adds an empty environment object under `config.json`:
  ```json
  "environments": {
    "go": {}
  }
  ```
- Used later by volume/image/serve commands.

---

### `darp add portmap`

```sh
darp add portmap <domain_name> <service_name> <host_port> <container_port>
```

- Adds a host→container port mapping for a specific service under a domain.
- `service_name` usually matches the project/folder name.
- Prevents overwriting an existing mapping on the same host port.

Config shape:

```json
"domains": {
  "projects": {
    "services": {
      "hello-world": {
        "host_portmappings": {
          "9000": "9001"
        }
      }
    }
  }
}
```

---

### `darp add volume`

```sh
darp add volume <environment> <container_dir> <host_dir>
```

- Associates a host directory with a container mount path for a given environment.
- `host_dir` may include the `{pwd}` token, which is replaced at runtime with the current directory when running `shell`/`serve`.

Example:

```sh
darp add volume go /extra '{pwd}/.cache'
```

Result:

```json
"environments": {
  "go": {
    "volumes": [
      {
        "container": "/extra",
        "host": "{pwd}/.cache"
      }
    ]
  }
}
```

Duplicate volume definitions for the same env/container/host are rejected.

---

## `darp rm ...`

Group of commands to remove configuration entries.

### `darp rm DARP_ROOT`

```sh
darp rm DARP_ROOT [-z PATH_TO_ZSHRC]
```

- Removes any `export DARP_ROOT=...` lines from `.zshrc` (default: `~/.zshrc`).
- Reminds you to restart shell or `source` the file.

---

### `darp rm PODMAN_MACHINE`

```sh
darp rm PODMAN_MACHINE [-z PATH_TO_ZSHRC]
```

- Removes any `export PODMAN_MACHINE=...` lines from `.zshrc`.

---

### `darp rm domain`

```sh
darp rm domain <name> [location]
```

- Deletes `domains[name]` from `config.json`.
- Optional `location` argument is ignored (kept for CLI compatibility).

---

### `darp rm environment`

```sh
darp rm environment <name>
```

- Removes an environment from `config.json`.
- If `environments` becomes empty, the key is removed entirely.

---

### `darp rm image_repository`

```sh
darp rm image_repository <environment>
```

- Removes `"image_repository"` from a specific environment.
- Requires that at least one environment currently has `image_repository` set or it will prompt you to `set image_repository` first.

---

### `darp rm portmap`

```sh
darp rm portmap <domain_name> <service_name> <host_port> [container_port]
```

- Deletes the host port mapping for a given domain/service/host_port.
- `container_port` argument is accepted but ignored for removal.

---

### `darp rm volume`

```sh
darp rm volume <environment> <container_dir> <host_dir>
```

- Removes a specific volume mapping from the environment.
- If no match is found, exits with a helpful message.
- Cleans up the `"volumes"` key if the list becomes empty.

---

### `darp rm serve_command`

```sh
darp rm serve_command <environment>
```

- Removes the `serve_command` from the environment.
- Only allowed if at least one environment has a `serve_command` defined (per spec).

---

## `darp urls`

**Purpose:** Show the list of reverse-proxied project URLs and their assigned ports.

**Usage:**

```sh
darp urls
```

**Behavior:**

- Reads `portmap.json`.
- Prints, for each domain:

  ```txt
  projects
    http://hello-world.projects.test (50100)
    http://other-project.projects.test (50101)
  ```

Helpful for copy-pasting URLs or checking which port a project is bound to.

---

## Behavior When No Command Is Given

- If you run `darp` with no arguments, it prints the top-level help and exits with code `0`.

If you run a group command without a subcommand, it prints that group’s help and exits with `1`:

- `darp add`
- `darp rm`
- `darp set`

---

## Engine Requirements & Checks

Before any engine-dependent command runs (`deploy`, `shell`, `serve`, etc.), darp:

1. Loads `config.json` and determines `ENGINE` (`docker` or `podman`).
2. For Docker:
   - Runs `docker info` to ensure the daemon is up.
3. For Podman:
   - Checks `podman machine list` and ensures the configured `PODMAN_MACHINE` is running.
   - Verifies `net.ipv4.ip_unprivileged_port_start <= 53` inside the machine; if not, suggests running `darp init`.

If misconfigured, darp prints hints such as:

- `Docker does not appear to be running (docker info)`
- `Podman machine 'podman-machine-default' appears to be down (podman machine start podman-machine-default)`

