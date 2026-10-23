"""News overlay `news-overlay-0.1.0` (Phase 04 round 2): a deterministic, VETO-ONLY reading of a NewsContext.

Intended trading role (reports/phase-04/NEWS_FORWARD_TEST_PLAN_FA.md): News may only make the system more cautious —
mark a NEWS_SHOCK state that blocks NEW entries for the affected asset, or nothing. It never creates a candidate,
never changes a price, quantity, stop or exit, never loosens a risk limit and never sizes up. It is NOT wired into the
order path in this round: forward SHADOW evaluation replays it offline against journaled decisions (variant C).

Rule (pre-registered for the forward test; no LLM, no provider sentiment, no inferred tickers): BLOCK_NEW_ENTRIES when
the context is enabled and contains a story published at most `shock_window` before the decision that is either
REGULATORY and mapped to the asset, or MACRO / REGULATORY with no mapped asset (global). Disabled context, archive or
provider errors and missing evidence give NO_EFFECT with the reason recorded (the overlay is not a data-quality gate).
"""

from datetime import timedelta
from typing import Literal

from wallex_agent.contracts import Contract

from .contracts import Asset, NewsContext

OVERLAY_VERSION = "news-overlay-0.1.0"
SHOCK_WINDOW = timedelta(hours=4)  # host-owned constant of this version; not a tuned strategy parameter


class NewsGate(Contract):
    version: Literal["news-overlay-0.1.0"] = OVERLAY_VERSION
    asset: Asset
    action: Literal["NO_EFFECT", "BLOCK_NEW_ENTRIES"]
    reason: str
    context_id: str
    story_ids: tuple[str, ...] = ()


def news_gate(ctx: NewsContext, asset: Asset, *, shock_window: timedelta = SHOCK_WINDOW) -> NewsGate:
    cid = ctx.context_id
    if not ctx.enabled:
        return NewsGate(asset=asset, action="NO_EFFECT", reason="DISABLED", context_id=cid)
    if any("UNAVAILABLE" in f or "ERROR" in f for f in ctx.flags):
        return NewsGate(asset=asset, action="NO_EFFECT", reason="NEWS_UNAVAILABLE", context_id=cid)
    hits: list[str] = []
    for story in ctx.stories:
        item = story.item
        if item.published_at is None or item.published_at > ctx.as_of or ctx.as_of - item.published_at > shock_window:
            continue
        mapped = asset in item.assets and item.category == "REGULATORY"
        global_ = not item.assets and item.category in ("MACRO", "REGULATORY")
        if mapped or global_:
            hits.append(item.id)
    if hits:
        return NewsGate(asset=asset, action="BLOCK_NEW_ENTRIES", reason="NEWS_SHOCK", context_id=cid, story_ids=tuple(hits))
    return NewsGate(asset=asset, action="NO_EFFECT", reason="NO_QUALIFYING_STORY", context_id=cid)
