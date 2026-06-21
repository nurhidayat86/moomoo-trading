"""EV/EBITDA, PEG, and PEGY computed from financials and relative valuation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from scanner.filters.financials import FinancialsSummary
from scanner.scoring import clamp_score, score_discount, weighted_score


@dataclass
class ExtendedMultiplesResult:
    ev_ebitda: float | None = None
    peg_ratio: float | None = None
    pegy_ratio: float | None = None
    extended_multiples_score: float = 0.0
    extended_multiples_reason: str = ""
    extended_multiples: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ev_ebitda": self.ev_ebitda,
            "peg_ratio": self.peg_ratio,
            "pegy_ratio": self.pegy_ratio,
            "extended_multiples_score": self.extended_multiples_score,
            "extended_multiples_reason": self.extended_multiples_reason,
        }


def _dividend_yield_pct(snapshot: dict[str, Any]) -> float | None:
    """Dividend yield as a percentage (e.g. 3.0 for 3%), matching earnings growth units."""
    price = float(snapshot.get("last_price") or 0)
    if price <= 0:
        return None
    dividend_ttm = float(snapshot.get("dividend_ttm") or 0)
    div_ratio = float(snapshot.get("dividend_ratio_ttm") or 0)
    dividend_yield = div_ratio / 100 if div_ratio > 1 else div_ratio
    if dividend_yield <= 0 and dividend_ttm > 0:
        dividend_yield = dividend_ttm / price
    if dividend_yield <= 0:
        return None
    return dividend_yield * 100


def evaluate_extended_multiples(
    financials: FinancialsSummary,
    snapshot: dict[str, Any],
    rel_dict: dict[str, Any],
    config: dict[str, Any],
) -> ExtendedMultiplesResult:
    cfg = config.get("valuation", {}).get("extended_multiples", {})
    if not cfg.get("enabled", True):
        return ExtendedMultiplesResult()

    mkt_cap = float(snapshot.get("total_market_val") or 0)
    ebitda = financials.ebitda_ttm
    net_debt = financials.net_debt or 0
    pe_ttm = rel_dict.get("pe_ttm")

    ev = None
    ev_ebitda = None
    if mkt_cap > 0:
        ev = mkt_cap + net_debt
        if ebitda and ebitda > 0:
            ev_ebitda = round(ev / ebitda, 2)

    peg = None
    growth_pct = financials.earnings_growth_pct
    if growth_pct is None and financials.revenue_cagr_3y is not None:
        growth_pct = financials.revenue_cagr_3y * 100
    if pe_ttm and growth_pct and growth_pct > 0:
        peg = round(float(pe_ttm) / growth_pct, 2)

    pegy = None
    yield_pct = _dividend_yield_pct(snapshot)
    growth_plus_yield = None
    if growth_pct is not None and growth_pct > 0:
        growth_plus_yield = growth_pct + (yield_pct or 0.0)
        if pe_ttm and growth_plus_yield > 0:
            pegy = round(float(pe_ttm) / growth_plus_yield, 2)

    parts: list[tuple[float | None, float]] = []
    reasons: list[str] = []

    ev_target = float(cfg.get("target_ev_ebitda", 15.0))
    if ev_ebitda is not None:
        ev_score = score_discount(ev_ebitda, ev_target, target_discount=0.25)
        if ev_score is not None:
            parts.append((ev_score, 0.50))
            reasons.append(f"EV/EBITDA {ev_ebitda:.1f}")

    if peg is not None:
        if peg <= 1.0:
            peg_score = clamp_score(100 - peg * 20)
        elif peg <= 2.0:
            peg_score = clamp_score(80 - (peg - 1) * 30)
        else:
            peg_score = clamp_score(max(0, 50 - (peg - 2) * 15))
        parts.append((peg_score, 0.50))
        reasons.append(f"PEG {peg:.2f}")

    score = weighted_score(parts) if parts else 0.0

    return ExtendedMultiplesResult(
        ev_ebitda=ev_ebitda,
        peg_ratio=peg,
        pegy_ratio=pegy,
        extended_multiples_score=score,
        extended_multiples_reason="; ".join(reasons) if reasons else "Insufficient EV/PEG data",
        extended_multiples={
            "ev": ev,
            "ebitda_ttm": ebitda,
            "pe_ttm": pe_ttm,
            "earnings_growth_used": growth_pct,
            "dividend_yield_used": yield_pct,
            "growth_plus_yield": growth_plus_yield,
            "peg": peg,
            "pegy": pegy,
        },
    )
