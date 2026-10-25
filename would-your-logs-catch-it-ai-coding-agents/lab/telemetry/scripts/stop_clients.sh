#!/bin/bash
# Runs INSIDE the VM before the client logs are copied (D24 point 1): stops every client, its detached daemons and the lab
# servers, waits for them to exit, and exits non-zero if any is still running or if it cannot tell, so that databases and logs
# are never copied while a writer may be alive. The patterns are written as [x]yz so that they never match this script's own
# command line.
#
# SAFETY: this script kills processes of the current user. It must never run on the host (on 17 Oct 2026 a test ran an earlier
# version on the host and closed the user's applications). It fails closed: it runs only when ALL of these hold, and any
# command that fails while checking them stops it before anything is killed:
#   - Q4_IN_VM=1 is set explicitly by the caller;
#   - the VM's marker file ~/.agentlab_env exists;
#   - the current user is the VM's lab user, "lab";
#   - hostname answers, and is not the lab host ("parrot").
refuse() { echo "refusing to run: $1" >&2; exit 3; }
[ "${Q4_IN_VM:-}" = "1" ] || refuse "Q4_IN_VM=1 not set"
[ -f "$HOME/.agentlab_env" ] || refuse "no VM marker file ~/.agentlab_env"
user=$(id -un) || refuse "id failed"
[ "$user" = "lab" ] || refuse "user is '$user', not the VM's lab user"
host=$(hostname) || refuse "hostname failed"
[ -n "$host" ] || refuse "empty hostname"
[ "$host" != "parrot" ] || refuse "this is the lab host"
uid=$(id -u) || refuse "id -u failed"

PATTERNS=('[c]laude' '[c]odex' '[g]emini' '[q]wen' '[c]ursor-agent' '[g]oose' '[q]3-server.mjs' '[m]arker-server.mjs')

# Prints the pids still alive; returns 2 if the processes could not be enumerated (pgrep: 0 = match, 1 = none, >1 = error).
alive() {
  local p out rc all=""
  for p in "${PATTERNS[@]}"; do
    out=$(pgrep -u "$uid" -f "$p"); rc=$?
    if [ "$rc" -gt 1 ]; then echo "pgrep failed with status $rc for $p" >&2; return 2; fi
    all="$all $out"
  done
  echo $all
}

tmux kill-server 2>/dev/null
for p in "${PATTERNS[@]}"; do pkill -TERM -u "$uid" -f "$p"; done
left=""
for i in $(seq 1 20); do
  left=$(alive) || { echo "cannot tell whether the clients stopped"; exit 1; }
  [ -z "$left" ] && break
  sleep 0.5
done
if [ -n "$left" ]; then
  for p in "${PATTERNS[@]}"; do pkill -KILL -u "$uid" -f "$p"; done
  sleep 2
  left=$(alive) || { echo "cannot tell whether the clients stopped"; exit 1; }
fi
if [ -n "$left" ]; then
  echo "still running: $left"
  ps -o pid=,args= -p "$(echo $left | tr ' ' ',')"
  exit 1
fi
sleep 1  # let the kernel flush what the writers left
echo "clients stopped"
