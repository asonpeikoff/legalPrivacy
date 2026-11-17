"""Recognise pages that need logging in (Facebook, Instagram, LinkedIn, X and the like).

Public pages are browsed as usual. When a page turns out to require an account, a
logged-out person glances at it and goes elsewhere, so the noise does the same: a short
look, no clicking further into it, and that site rests for a while. It never counts as
a failure.
"""
from __future__ import annotations

import re

_URL = re.compile(
    r"(/login\b|/log-in\b|/signin\b|/sign-in\b|/sign_in\b|login\.php|/accounts/login|/i/flow/login|"
    r"/authwall|/checkpoint/|/uas/login|/auth/login|servicelogin|/session/new|/users/sign_in|"
    r"[?&](next|redirect|continue|return_to|returnurl)=[^&]*(login|signin))", re.I)
_TEXT = re.compile(
    r"(log ?in to (continue|see|view|follow|keep)|sign ?in to (continue|see|view|follow|keep)|"
    r"you must (log|sign) ?in|log into (facebook|instagram|your account)|join (linkedin )?to (see|view)|"
    r"create an account (or log in )?to (continue|see|view)|this (page|content) (isn'?t|is not) available "
    r"(right now|unless you'?re logged in)|sign up to see|see more on (facebook|instagram))", re.I)
_PASSWORD = re.compile(r"<input[^>]+type\s*=\s*[\"']?password", re.I)


def looks_like_login_wall(url: str, title: str = "", text: str = "", html: str = "",
                          password_fields: int | None = None) -> bool:
    """True when this page is a sign-in page or hides its content behind one.

    url: where the browser ended up (after any redirect); title/text: page title and the
    first part of the visible text; html: page source (used when text isn't available);
    password_fields: visible password boxes, if the caller could count them."""
    if _URL.search(url or ""):
        return True
    blob = f"{title} {text}"
    if _TEXT.search(blob):
        return True
    if not text and html:
        if _TEXT.search(html[:200_000]):
            return True
        pw = len(_PASSWORD.findall(html[:200_000]))
        # a password box on a page with little else = a sign-in page
        visible_words = len(re.sub(r"<[^>]+>", " ", html[:200_000]).split())
        return pw > 0 and visible_words < 600
    if password_fields:
        return len((text or "").split()) < 400
    return False
