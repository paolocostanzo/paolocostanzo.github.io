#!/bin/bash
. ~/.agentlab_env
cd ~
for i in 1 2 3; do
  python3 - <<PY
import sys; sys.path.insert(0, "/home/lab/lab/scripts")
import run_q5_matrix as r
r.reset_claude(); r.regenerate_repo("/home/lab/lab"); r.reset_log()
PY
  T=$(python3 -c "import json;d=json.load(open(/home/lab/.claude.json));print(d.get(projects,{}).get(/home/lab/q5-repo,absent))")
  ( cd ~/q5-repo && timeout 90 claude -p "say ok" > ~/claude-p-clean-r$i.out 2>&1; echo "exit=$?" >> ~/claude-p-clean-r$i.out )
  sleep 2; pkill -f marker-server.mjs
  echo "r$i entry_before=$T starts=$(grep -c "\"event\":\"start\"" ~/canary-start.log) $(claude --version) :: $(head -c 100 ~/claude-p-clean-r$i.out | tr "\n" " ")"
done
