#!/usr/bin/env bash
#
# preflight.sh - Stingray system preflight check
#
# Produces machine-readable status (JSON) and human-readable failure messages.
# Returns nonzero if any mandatory device node or critical check fails.
#
# Usage: scripts/preflight.sh [--json]
#   --json   Emit machine-readable JSON to stdout (default: human-readable)
#
set -euo pipefail

JSON_MODE=0
if [[ "${1:-}" == "--json" ]]; then
  JSON_MODE=1
fi

FAILURES=()
CHECKS=()

# --- Helper: record a check result ---
record() {
  local name="$1" status="$2" detail="$3"
  CHECKS+=("\"${name}\": {\"status\": \"${status}\", \"detail\": \"${detail}\"}")
  if [[ "$status" == "fail" ]]; then
    FAILURES+=("$name: $detail")
  fi
}

# --- 1. OS / kernel ---
OS_NAME=$(grep PRETTY_NAME /etc/os-release 2>/dev/null | cut -d'"' -f2 || echo "unknown")
KERNEL=$(uname -r 2>/dev/null || echo "unknown")
ARCH=$(uname -m 2>/dev/null || echo "unknown")
MODEL=$(cat /proc/device-tree/model 2>/dev/null | tr -d "\0" || echo "unknown")
record "os" "ok" "$OS_NAME / $KERNEL / $ARCH / $MODEL"

# --- 2. Memory ---
MEM_TOTAL=$(free -h 2>/dev/null | awk "/^Mem:/{print \$2}" || echo "unknown")
record "memory" "ok" "total=$MEM_TOTAL"

# --- 3. Storage ---
ROOT_AVAIL=$(df -h / 2>/dev/null | awk "NR==2{print \$4}" || echo "unknown")
ROOT_USE=$(df -h / 2>/dev/null | awk "NR==2{print \$5}" || echo "unknown")
record "storage" "ok" "root avail=$ROOT_AVAIL use=$ROOT_USE"

# --- 4. I2C device nodes ---
# Mandatory: at least one /dev/i2c-* node must exist.
I2C_NODES=$( { ls /dev/i2c-* 2>/dev/null || true; } | wc -l )
if [[ "$I2C_NODES" -gt 0 ]]; then
  I2C_LIST=$( { ls /dev/i2c-* 2>/dev/null || true; } | tr "\n" " ")
  record "i2c_devices" "ok" "found $I2C_NODES node(s): $I2C_LIST"
else
  record "i2c_devices" "fail" "no /dev/i2c-* nodes found (I2C not enabled in config.txt?)"
fi

# --- 5. SPI device nodes ---
SPI_NODES=$( { ls /dev/spidev* 2>/dev/null || true; } | wc -l )
if [[ "$SPI_NODES" -gt 0 ]]; then
  SPI_LIST=$( { ls /dev/spidev* 2>/dev/null || true; } | tr "\n" " " )
  record "spi_devices" "ok" "found $SPI_NODES node(s): $SPI_LIST"
else
  record "spi_devices" "warn" "no /dev/spidev* nodes found (SPI not enabled or no display attached)"
fi

# --- 6. Throttling ---
THROTTLED=$(vcgencmd get_throttled 2>/dev/null | cut -d"=" -f2 || echo "unknown")
if [[ "$THROTTLED" == "0x0" ]]; then
  record "throttling" "ok" "throttled=0x0 (no throttling)"
else
  record "throttling" "fail" "throttled=$THROTTLED (undervoltage/throttling detected)"
fi

# --- 7. Temperature ---
TEMP=$(vcgencmd measure_temp 2>/dev/null | cut -d"=" -f2 | tr -d "'" || echo "unknown")
record "temperature" "ok" "temp=$TEMP"

# --- 8. Failed systemd services ---
# Count lines that are actual failed units (start with a space + unit name)
FAILED_COUNT=$(systemctl --failed --no-pager 2>/dev/null | grep -cE '^\s+\S+\.service' || true)
if [[ "$FAILED_COUNT" -eq 0 ]]; then
  record "systemd" "ok" "no failed services"
else
  record "systemd" "warn" "$FAILED_COUNT failed service(s)"
fi

# --- 9. Uptime ---
UPTIME=$(uptime -p 2>/dev/null || echo "unknown")
record "uptime" "ok" "$UPTIME"

# --- 10. User groups (i2c, spi, gpio) ---
MY_GROUPS=$(id -nG 2>/dev/null || echo "")
MISSING_GROUPS=()
for g in i2c spi gpio; do
  if ! echo "$MY_GROUPS" | grep -qw "$g"; then
    MISSING_GROUPS+=("$g")
  fi
done
if [[ ${#MISSING_GROUPS[@]} -eq 0 ]]; then
  record "user_groups" "ok" "user has i2c, spi, gpio groups"
else
  record "user_groups" "warn" "user missing groups: ${MISSING_GROUPS[*]}"
fi

# --- Output ---
if [[ "$JSON_MODE" -eq 1 ]]; then
  # Build the checks JSON body (comma-separated)
  CHECKS_JSON=""
  for c in "${CHECKS[@]}"; do
    if [[ -n "$CHECKS_JSON" ]]; then
      CHECKS_JSON="$CHECKS_JSON,"
    fi
    CHECKS_JSON="$CHECKS_JSON
    $c"
  done

  # Build the failures JSON array
  FAILURES_JSON=""
  for f in "${FAILURES[@]}"; do
    if [[ -n "$FAILURES_JSON" ]]; then
      FAILURES_JSON="$FAILURES_JSON,"
    fi
    FAILURES_JSON="$FAILURES_JSON
    \"$f\""
  done

  echo "{"
  echo "  \"timestamp\": \"$(date -Is)\","
  echo "  \"checks\": {"
  echo "$CHECKS_JSON"
  echo "  },"
  if [[ ${#FAILURES[@]} -eq 0 ]]; then
    echo "  \"result\": \"PASS\""
  else
    echo "  \"result\": \"FAIL\","
    echo "  \"failures\": ["
    echo "$FAILURES_JSON"
    echo "  ]"
  fi
  echo "}"
else
  echo "===== STINGRAY PREFLIGHT ====="
  echo "Timestamp: $(date -Is)"
  echo "OS: $OS_NAME"
  echo "Kernel: $KERNEL"
  echo "Arch: $ARCH"
  echo "Model: $MODEL"
  echo
  for c in "${CHECKS[@]}"; do
    name=$(echo "$c" | sed -n "s/\"\\([^\"]*\\)\": {\"status\".*/\\1/p")
    status=$(echo "$c" | sed -n "s/.*\"status\": \"\\([^\"]*\\)\".*/\\1/p")
    detail=$(echo "$c" | sed -n "s/.*\"detail\": \"\\([^\"]*\\)\".*/\\1/p")
    marker="OK  "
    [[ "$status" == "fail" ]] && marker="FAIL"
    [[ "$status" == "warn" ]] && marker="WARN"
    printf "%-4s %-16s %s\n" "$marker" "$name" "$detail"
  done
  echo
  if [[ ${#FAILURES[@]} -eq 0 ]]; then
    echo "Result: PASS"
  else
    echo "Result: FAIL"
    for f in "${FAILURES[@]}"; do
      echo "  - $f"
    done
  fi
fi

# Exit code: nonzero if any hard failure
if [[ ${#FAILURES[@]} -gt 0 ]]; then
  exit 1
fi
exit 0
