#!/usr/bin/env python3
"""Runs INSIDE the VM next to a run or session (D25 point 5): reads the client's tmux pane every second, read-only, and records
every quota or rate-limit message it sees, with the screen, even if the client recovers and the message is gone by the next saved
capture. The historical Q3 and Q5 runners are left as they ran; this observer is the Q4 addition.

  python3 quota_watch.py --client codex --session q3run --out ~/q4-out/quota-watch.jsonl &

Records (JSON Lines): "started"; "heartbeat" every HEARTBEAT_S seconds with the number of captures, whether the client's tmux
session existed (between a client's restarts it does not: an expected gap, not an error) and capture failures; "capture-error"
when the session existed but could not be read; "vanished" when it existed at the check and was gone at the read (the runner
closed it in between, D32); "quota" with the screen; "stopped" on SIGTERM (the orderly end). The host
(observer_problems() in q4_events.py) rejects a run without "stopped", with a gap between records longer than MAX_GAP_S, or with
capture errors: an observer that died part-way must not look like an observer that saw nothing.
"""
import argparse, importlib.util, json, signal, subprocess, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("quota", HERE / "quota.py")
quota = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(quota)


HEARTBEAT_S = 10


# tmux's own words for a session or server that does not exist; any other failure is a read error (Codex review of D32, D32-01)
ABSENT = ("no server running", "can't find session", "session not found", "no such file or directory")


def absent(stderr):
    return any(a in (stderr or "").lower() for a in ABSENT)


def screen(session):
    """(present, text, error): present is False when the session does not exist (between client restarts); a session that existed
    at the check and was confirmed gone at the read gives (False, "", VANISHED); a failure that does not establish absence is an
    error, with tmux's messages."""
    h = subprocess.run(["tmux", "has-session", "-t", session], capture_output=True, text=True)
    if h.returncode != 0:
        if absent(h.stderr):
            return False, "", None
        return True, "", f"has-session: {h.stderr.strip() or f'exit {h.returncode}'}"
    r = subprocess.run(["tmux", "capture-pane", "-p", "-J", "-t", session], capture_output=True, text=True)
    if r.returncode == 0:
        return True, r.stdout, None
    err = r.stderr.strip() or f"exit {r.returncode}"
    again = subprocess.run(["tmux", "has-session", "-t", session], capture_output=True, text=True)
    if again.returncode != 0 and absent(again.stderr):
        # the session ended between the two calls (the runner closes tmux at the end of a run or cycle): recorded as "vanished",
        # not as a read error and not silently (D32)
        return False, "", VANISHED
    return True, "", f"capture-pane: {err}; recheck: {again.stderr.strip() or f'exit {again.returncode}'}"


VANISHED = "vanished"  # marker: the session existed when checked and was gone when its screen was read (D32)


class Stop(Exception):
    pass


def watch(client, session, out, interval=1.0, rounds=None, read=screen, clock=time.time, sleep=time.sleep):
    out = Path(out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    stats = {"captures": 0, "absent": 0, "capture_errors": 0, "vanished": 0}

    def write(fh, **rec):
        fh.write(json.dumps({"ts": clock(), **rec}) + "\n")
        fh.flush()

    def on_term(signum, frame):
        raise Stop()

    with open(out, "a") as fh:
        write(fh, event="started", client=client, session=session)
        old = signal.signal(signal.SIGTERM, on_term) if rounds is None else None
        last, last_beat, n = None, clock(), 0
        try:
            while rounds is None or n < rounds:
                n += 1
                present, s, err = read(session)
                if not present and err == VANISHED:
                    stats["vanished"] += 1
                    write(fh, event="vanished")
                elif not present:
                    stats["absent"] += 1
                elif err:
                    stats["capture_errors"] += 1
                    write(fh, event="capture-error", error=err)
                else:
                    stats["captures"] += 1
                    msg = quota.find(client, s)
                    if msg and s != last:
                        write(fh, event="quota", message=msg, screen=s)
                        last = s
                if clock() - last_beat >= HEARTBEAT_S:
                    write(fh, event="heartbeat", **stats)
                    last_beat = clock()
                sleep(interval)
        except Stop:
            if old is not None:
                signal.signal(signal.SIGTERM, old)
            write(fh, event="stopped", **stats)  # the orderly end: only on SIGTERM from the wrapper
            return 0
        except BaseException as exc:  # anything else is a failure, never an orderly end
            write(fh, event="failed", error=f"{type(exc).__name__}: {exc}", **stats)
            return 1
        write(fh, event="stopped", **stats)  # bounded test loops (rounds) end here
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    raise SystemExit(watch(a.client, a.session, a.out))


if __name__ == "__main__":
    main()
