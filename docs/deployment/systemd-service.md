# Stingray Systemd Service — Deployment Guide

This document describes how the stingray application is packaged and deployed
as a systemd service on the Raspberry Pi (`stingray` host).

## Overview

The stingray application runs as a systemd service managed by `monusr`, an
unprivileged system user. The service:

- Starts automatically on boot
- Auto-restarts on failure (capped at 5 restarts per 60 seconds)
- Logs to the systemd journal (no separate log file)
- Runs with minimal privileges (no root, no sandbox restrictions)

## Prerequisites

Before deploying the service, ensure the following are in place on the Pi:

1. **I2C enabled** in `/boot/firmware/config.txt`:
   ```
   dtparam=i2c_arm=on
   ```

2. **SPI enabled** in `/boot/firmware/config.txt`:
   ```
   dtparam=spi=on
   ```

3. **I2C kernel module loads at boot** via `/etc/modules-load.d/i2c-dev.conf`:
   ```
   i2c-dev
   ```

4. **Python dependencies installed**:
   ```sh
   cd /home/ravik/src_git/project_stingray
   uv sync --extra hardware
   uv pip install RPi.GPIO
   ```

5. **DejaVu fonts installed** (for the TFT display):
   ```sh
   sudo apt install -y fonts-dejavu-core
   ```

## Service User

The service runs as `monusr`, a dedicated system user with no login shell.

### Creating the user

```sh
sudo useradd --system --no-create-home --shell /usr/sbin/nologin monusr
sudo usermod -aG i2c,spi,gpio monusr
```

### Required groups

| Group | Purpose |
|---|---|
| `i2c` | Access to `/dev/i2c-1` (sensor bus) |
| `spi` | Access to `/dev/spidev0.0` (display) |
| `gpio` | Access to `/dev/mem` via RPi.GPIO (display control pins) |

## File Ownership

The service user must own the project directory and data directory:

```sh
sudo chown -R monusr:monusr /home/ravik/src_git/project_stingray
sudo chown -R monusr:monusr /mnt/stingray
```

The parent directories must be traversable by `monusr`:

```sh
sudo chmod o+rx /home/ravik
sudo chmod o+rx /home/ravik/src_git
```

## Log Directory

The application writes logs to `/var/log/stingray` (configured in
`application.combined.yaml`). Create the directory and set ownership:

```sh
sudo mkdir -p /var/log/stingray
sudo chown monusr:monusr /var/log/stingray
```

## Systemd Unit File

Location: `/etc/systemd/system/stingray.service`

```ini
[Unit]
Description=Stingray Air Quality Monitor
After=network.target
Wants=network-online.target
StartLimitBurst=5
StartLimitIntervalSec=60

[Service]
User=monusr
WorkingDirectory=/home/ravik/src_git/project_stingray
ExecStart=/home/ravik/src_git/project_stingray/.venv/bin/python -m stingray.main --config /home/ravik/src_git/project_stingray/config/application.combined.yaml
Restart=on-failure
RestartSec=5
Environment=PATH=/home/ravik/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal
SyslogIdentifier=stingray

[Install]
WantedBy=multi-user.target
```

### Key settings

| Setting | Value | Purpose |
|---|---|---|
| `User` | `monusr` | Run as unprivileged user |
| `WorkingDirectory` | project root | Relative paths resolve correctly |
| `ExecStart` | venv python + module | Uses the project's virtual environment |
| `Restart` | `on-failure` | Auto-recover from crashes |
| `RestartSec` | `5` | Wait 5s before restarting |
| `StartLimitBurst` | `5` | Max 5 restarts... |
| `StartLimitIntervalSec` | `60` | ...per 60 seconds |
| `PYTHONUNBUFFERED` | `1` | Log to journal immediately |
| `StandardOutput/Error` | `journal` | All output goes to systemd journal |

## Deployment Steps

Execute these steps on the Pi in order:

```sh
# 1. Create the service user
sudo useradd --system --no-create-home --shell /usr/sbin/nologin monusr
sudo usermod -aG i2c,spi,gpio monusr

# 2. Set file ownership
sudo chown -R monusr:monusr /home/ravik/src_git/project_stingray
sudo chown -R monusr:monusr /mnt/stingray
sudo chmod o+rx /home/ravik
sudo chmod o+rx /home/ravik/src_git

# 3. Create the log directory
sudo mkdir -p /var/log/stingray
sudo chown monusr:monusr /var/log/stingray

# 4. Write the systemd unit file
sudo tee /etc/systemd/system/stingray.service << 'EOF'
[Unit]
Description=Stingray Air Quality Monitor
After=network.target
Wants=network-online.target
StartLimitBurst=5
StartLimitIntervalSec=60

[Service]
User=monusr
WorkingDirectory=/home/ravik/src_git/project_stingray
ExecStart=/home/ravik/src_git/project_stingray/.venv/bin/python -m stingray.main --config /home/ravik/src_git/project_stingray/config/application.combined.yaml
Restart=on-failure
RestartSec=5
Environment=PATH=/home/ravik/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal
SyslogIdentifier=stingray

[Install]
WantedBy=multi-user.target
EOF

# 5. Ensure I2C module loads at boot
sudo tee /etc/modules-load.d/i2c-dev.conf << 'EOF'
i2c-dev
EOF

# 6. Kill any existing manual process
pkill -f '[s]tingray.main'

# 7. Reload systemd and enable the service
sudo systemctl daemon-reload
sudo systemctl enable stingray

# 8. Start the service
sudo systemctl start stingray

# 9. Verify
systemctl status stingray
```

## Operations

### Check status

```sh
systemctl status stingray
```

### View logs

```sh
# Live logs (follow)
journalctl -u stingray -f

# Last 50 lines
journalctl -u stingray --no-pager -n 50

# Logs from the last hour
journalctl -u stingray --since "1 hour ago"

# Logs since the last boot
journalctl -u stingray --boot
```

### Restart

```sh
sudo systemctl restart stingray
```

### Stop

```sh
sudo systemctl stop stingray
```

### Disable (prevent auto-start on boot)

```sh
sudo systemctl disable stingray
```

### Re-enable

```sh
sudo systemctl enable stingray
```

### Check resource usage

```sh
systemctl status stingray   # shows CPU, memory, task count
```

## Updating the Application

To deploy a new version:

```sh
# 1. Pull the latest code
cd /home/ravik/src_git/project_stingray
git pull

# 2. Sync dependencies (if pyproject.toml changed)
uv sync --extra hardware

# 3. Restart the service
sudo systemctl restart stingray

# 4. Verify
systemctl status stingray
journalctl -u stingray --no-pager -n 20
```

## Troubleshooting

### Service fails to start

```sh
# Check the journal for error details
journalctl -u stingray --no-pager -n 50

# Common causes:
# - Missing I2C module: sudo modprobe i2c-dev
# - Missing dependencies: uv sync --extra hardware
# - Permission denied: check file ownership and group membership
# - Port already in use: check if another process is on port 8081
```

### Display not initializing

```sh
# Check the journal for display errors
journalctl -u stingray --no-pager | grep -i display

# Common causes:
# - SPI not enabled: check /boot/firmware/config.txt
# - Missing RPi.GPIO: uv pip install RPi.GPIO
# - Missing fonts: sudo apt install -y fonts-dejavu-core
# - GPIO access denied: check monusr is in the gpio group
```

### Sensors not detected

```sh
# Check the journal for sensor errors
journalctl -u stingray --no-pager | grep -i sensor

# Verify I2C devices are visible
sudo i2cdetect -y 1

# Expected addresses:
# - SPS30: 0x69
# - SCD4X: 0x62
# - SHT45: 0x44
# - SGP41: 0x59
```

### Service restart loop

If the service is stuck in a restart loop:

```sh
# Stop the service
sudo systemctl stop stingray

# Check the journal for the root cause
journalctl -u stingray --no-pager -n 100

# Fix the issue, then reset the failure counter and restart
sudo systemctl reset-failed stingray
sudo systemctl start stingray
```

## Security Notes

- The service runs as `monusr`, not root
- `monusr` has no login shell and no home directory
- The service has no sandbox restrictions (no `ProtectSystem`, `PrivateTmp`, etc.)
  because the application needs access to `/home`, `/mnt`, and device nodes
- For a more hardened deployment, consider:
  - Moving the project to `/opt/stingray`
  - Adding `ProtectSystem=full` and `ReadWritePaths=` for specific directories
  - Adding `NoNewPrivileges=true`
  - Adding `MemoryMax=512M`

## Future Improvements

- [ ] Move project to `/opt/stingray` for cleaner separation
- [ ] Add `ProtectSystem=full` and `ReadWritePaths=` for sandboxing
- [ ] Add a health check service that curls the API
- [ ] Add log rotation if moving off journal
- [ ] Create a `.deb` package for cleaner deployment
- [ ] Add `uv tool install` as an alternative to the project venv
