#!/usr/bin/env python3
"""Recognise a client's quota or rate-limit messages in screen text (plan of 17 Oct 2026, phase 0.4; Paolo: "stop if they run out").

A session or run in which one of these appears is not valid: the client could not do what was asked, so its logs do not show
normal use, nor an event handled as the client would handle it. The orchestrators keep such an attempt and stop the campaign.
Patterns are deliberately specific (a quota word together with a limit word, or an HTTP 429 in context), so that ordinary text
about the test repository does not match; they are checked on the exploratory set-up sessions before the campaign.
"""
import re

GENERIC = [
    # not Codex CLI's start-up notice "You have 2 usage limit resets available" (baseline of 23 Oct 2026, D32)
    r"\busage limit\b(?!\s+resets\s+available\b)", r"\brate[- ]limit(ed)?\b", r"\bquota (exceeded|exhausted|reached)\b", r"\bexceeded (your|the) (current )?quota\b",
    r"\bRESOURCE_EXHAUSTED\b", r"\b(HTTP|status|error|code)[: ]+429\b", r"\b429 Too Many Requests\b", r"\btoo many requests\b",
    r"\bout of credits\b", r"\bcredit balance is too low\b",
    r"\bprepayment credits are depleted\b",  # the Gemini key's prepaid project when its credit is used up (D35)
]
# Notices that only report usage, removed before matching: Claude Code's footer "You've used 76% of your weekly limit · resets
# Oct 24, 10pm" (probe of 23 Oct 2026, D35); spacing and line wraps tolerated
NOTICES = {
    "claude-code": [r"You've\s+used\s+\d+%\s+of\s+your\s+[^·\n]*?(?:\n[^·\n]*?)?limit"],
    # Codex CLI's tip "… access available usage limit resets." (baseline-r3, D37), also when wrapped; only this recorded phrase is
    # removed, the rest of its line is still read (Codex review of D37: a tip line may carry a real failure)
    "codex": [r"available\s+usage\s+limit\s+resets"],
}
CLIENT = {
    "claude-code": [r"Claude usage limit reached", r"\b5-hour limit\b", r"\bweekly limit\b", r"\blimit will reset\b",
                    r"You've hit your (?:\w+ )?limit"],
    "codex": [r"You've hit your usage limit", r"\btry again at \d{1,2}:\d{2}\b"],
    "gemini": [r"\bQuota exceeded\b"],
    "qwen": [r"\bQuota exceeded\b"],
    "cursor-agent": [r"\bout of fast requests\b", r"\breached your usage\b"],
    "goose": [],
}


def compiled(client):
    return re.compile("|".join(GENERIC + CLIENT.get(client, [])), re.I)


def find(client, text):
    """The first quota or rate-limit message in text, or None."""
    text = text or ""
    for notice in NOTICES.get(client, []):
        text = re.sub(notice, " ", text, flags=re.I)
    m = compiled(client).search(text)
    return m.group(0) if m else None
