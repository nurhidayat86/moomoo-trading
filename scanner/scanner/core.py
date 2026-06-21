"""Scanner pipeline orchestration."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from scanner.api_client import MoomooAPIClient
from scanner.filters.strategy_matcher import match_strategy
from scanner.iv_cache import IVHistoryCache
from scanner.options import evaluate_options_overlay
from scanner.scoring import weighted_score
from scanner.technicals import evaluate_technicals
from scanner.universe import load_universe
from scanner.valuation import run_valuation_phase

logger = logging.getLogger("moomoo_scanner.core")


def _entry_score(row: dict[str, Any], config: dict[str, Any]) -> float:
    weights = config.get("scoring", {}).get("weights", {})
    return weighted_score(
        [
            (float(row.get("valuation_score") or 0), float(weights.get("valuation", 0.40))),
            (float(row.get("technical_score") or 0), float(weights.get("technical", 0.30))),
            (float(row.get("options_score") or 0), float(weights.get("options_liquidity", 0.20))),
            (float(row.get("iv_setup_score") or 0), float(weights.get("iv_setup", 0.10))),
        ]
    )


class OptionsStockScanner:
    """Production scanner pipeline for options candidate discovery."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.iv_cache = IVHistoryCache(config)

    def run(self, client: MoomooAPIClient | None = None) -> pd.DataFrame:
        mock_enabled = bool(self.config.get("mock", {}).get("enabled", False))
        own_client = client is None and not mock_enabled

        if own_client:
            client = MoomooAPIClient(self.config)
            client.connect()

        try:
            universe = load_universe(client, self.config)
            logger.info("Universe size: %s", len(universe))

            valuation_rows = run_valuation_phase(client, universe, self.config) if client else []
            if mock_enabled and not valuation_rows:
                valuation_rows = self._mock_valuation_rows(universe)

            logger.info("Scored %s symbols in valuation phase", len(valuation_rows))

            final_rows: list[dict[str, Any]] = []
            for row in valuation_rows:
                code = str(row["code"])
                spot = float(row.get("price") or 0)

                if client and spot > 0:
                    options = evaluate_options_overlay(
                        client, code, spot, self.iv_cache, self.config
                    )
                    technicals = evaluate_technicals(client, code, self.config)
                else:
                    options = self._mock_options_row(code)
                    technicals = self._mock_technicals_row(code)

                merged = {**row, **options, **technicals}
                merged["entry_score"] = _entry_score(merged, self.config)
                strategy = match_strategy(merged, self.config)
                merged["recommended_strategies"] = strategy.strategies
                merged["strategy_rationale"] = strategy.rationale
                merged["iv_environment"] = strategy.iv_environment
                final_rows.append(merged)

            df = pd.DataFrame(final_rows)
            if not df.empty and "entry_score" in df.columns:
                df = df.sort_values("entry_score", ascending=False, na_position="last")
            return df.reset_index(drop=True)
        finally:
            if own_client and client is not None:
                client.close()

    @staticmethod
    def _mock_valuation_rows(universe: list[str]) -> list[dict[str, Any]]:
        rows = []
        for i, code in enumerate(universe):
            rel = max(40, 85 - i * 3)
            dcf = max(35, 80 - i * 2)
            ddm = 0.0
            intrinsic = dcf
            analyst = 60.0
            growth = 55.0
            extended = 50.0
            earnings = 50.0
            quality = 1.05
            fundamental = 62.0
            valuation = (
                rel * 0.20
                + 72.0 * 0.15
                + intrinsic * 0.20
                + analyst * 0.12
                + growth * 0.08
                + extended * 0.05
                + earnings * 0.05
                + fundamental * 0.15
            ) * quality
            rows.append(
                {
                    "code": code,
                    "name": code.split(".")[-1],
                    "price": 100 + i,
                    "market_cap": 5e11,
                    "pe_ttm": 18.0,
                    "pb_ttm": 3.2,
                    "ps_ttm": 4.0,
                    "pe_historical_avg": 22.0,
                    "pe_percentile": 35.0,
                    "pe_industry_median": 24.0,
                    "ms_fair_value": 140.0,
                    "ms_star_rating": 4,
                    "analyst_target_avg": 135.0,
                    "analyst_ratings_count": 2,
                    "analyst_top_stock_success_rate": 72.0,
                    "analyst_median_target": 138.0,
                    "analyst_weighted_target": 137.5,
                    "analyst_score": 65.0,
                    "analyst_mos_pct": 8.0,
                    "analyst_confidence": 72.0,
                    "analyst_stale": False,
                    "analyst_pct_above_price": 80.0,
                    "recent_n": 2,
                    "recent_tgt_min": 130.0,
                    "recent_tgt_max": 145.0,
                    "recent_tgt_avg": 137.5,
                    "recent_sr_min": 60.0,
                    "recent_sr_max": 72.0,
                    "recent_sr_avg": 66.0,
                    "recent_date_min": "2026-01-10",
                    "recent_date_max": "2026-02-01",
                    "recent_date_median": "2026-01-20",
                    "topsr_n": 2,
                    "topsr_tgt_min": 132.0,
                    "topsr_tgt_max": 144.0,
                    "topsr_tgt_avg": 138.0,
                    "topsr_sr_min": 65.0,
                    "topsr_sr_max": 75.0,
                    "topsr_sr_avg": 70.0,
                    "topsr_date_min": "2026-01-05",
                    "topsr_date_max": "2026-01-28",
                    "topsr_date_median": "2026-01-16",
                    "quality_factor": 1.05,
                    "growth_context_score": 60.0,
                    "growth_earnings_trend": "stable",
                    "growth_value_trap_flag": False,
                    "extended_multiples_score": 55.0,
                    "ev_ebitda": 14.0,
                    "peg_ratio": 1.2,
                    "pegy_ratio": 1.1,
                    "fundamental_score": fundamental,
                    "fcf_yield": 0.055,
                    "roic": 0.18,
                    "roe": 0.22,
                    "net_margin_ttm": 0.21,
                    "debt_to_equity": 0.6,
                    "net_debt_to_ebitda": 1.4,
                    "leverage_flag": False,
                    "fcf_trend": "improving",
                    "revenue_trend": "improving",
                    "margin_trend": "stable",
                    "earnings_risk_score": 52.0,
                    "dcf_data_source": "snapshot_proxy",
                    "ms_score": 72.0,
                    "relative_score": rel,
                    "relative_reason": "mock valuation detail",
                    "dcf_intrinsic": 140.0,
                    "dcf_mos": 0.35,
                    "dcf_wacc": 0.09,
                    "dcf_score": dcf,
                    "ddm_intrinsic": None,
                    "ddm_mos": None,
                    "dividend_yield": 0.005,
                    "ddm_score": ddm,
                    "intrinsic_score": intrinsic,
                    "best_intrinsic_value": 140.0,
                    "best_margin_of_safety": 0.35,
                    "price_to_fair_value": round((100 + i) / 140.0, 3),
                    "valuation_score": round(valuation, 2),
                    "valuation_score_breakdown": {"pillars": {}, "pre_multiplier": valuation, "quality_factor": 1.05, "final": valuation},
                    "analyst_ratings_summary": {"count": 2, "fetched_pages": 1, "truncated": False},
                    "analyst_ratings": [
                        {
                            "analyst": {
                                "uid": "mock-1",
                                "name": "Mock Analyst",
                                "stars": 4.0,
                                "success_rate": 65.0,
                                "stock_success_rate": 72.0,
                            },
                            "broker": {"name": "Mock Broker", "source": "Mock"},
                            "ratings": [
                                {
                                    "target_price": 138.0,
                                    "rating": 4,
                                    "rating_label": "Buy",
                                    "recommendation_date": "2026-01-15",
                                }
                            ],
                        }
                    ],
                }
            )
        return rows

    @staticmethod
    def _mock_options_row(code: str) -> dict[str, Any]:
        return {
            "code": code,
            "total_open_interest": 5000,
            "atm_bid_ask_spread_pct": 0.03,
            "liquidity_score": 92.0,
            "iv_setup_score": 88.0,
            "options_score": 90.8,
            "iv_current": 0.28,
            "iv_rank": 22.0,
            "iv_percentile": 25.0,
            "iv_environment": "low",
        }

    @staticmethod
    def _mock_technicals_row(code: str) -> dict[str, Any]:
        return {
            "code": code,
            "price": 100.0,
            "ema_fast": 98.0,
            "ema_slow": 95.0,
            "rsi": 42.0,
            "rvol": 1.25,
            "adv_usd": 50_000_000.0,
            "accumulation_ratio": 1.15,
            "volume_recovery_confirmed": True,
            "volume_score_bonus": 13.0,
            "price_above_ema_fast": True,
            "rsi_oversold_recovery": False,
            "bullish_turnaround": True,
            "technicals_stabilizing": True,
            "technical_score": 75.0,
        }


def export_results(df: pd.DataFrame, config: dict[str, Any]) -> dict[str, str]:
    output_cfg = config.get("output", {})
    json_path = Path(output_cfg.get("json_path", "scanner_output.json"))
    csv_path = Path(output_cfg.get("csv_path", "scanner_output.csv"))

    if not json_path.is_absolute():
        json_path = Path(__file__).resolve().parents[1] / json_path
    if not csv_path.is_absolute():
        csv_path = Path(__file__).resolve().parents[1] / csv_path

    records = df.to_dict(orient="records") if not df.empty else []
    json_path.write_text(json.dumps(records, indent=2, default=str), encoding="utf-8")

    csv_df = df.copy()
    for col in (
        "morningstar_report",
        "analyst_consensus",
        "valuation_detail",
        "analyst_ratings_summary",
        "valuation_score_breakdown",
        "quality_assessment",
        "growth_context",
        "extended_multiples",
        "fundamental_quality",
        "financials_summary",
        "earnings_reaction",
    ):
        if col in csv_df.columns:
            csv_df[col] = csv_df[col].apply(
                lambda v: json.dumps(v, default=str) if isinstance(v, dict) else v
            )
    if "analyst_ratings" in csv_df.columns:
        csv_df["analyst_ratings"] = csv_df["analyst_ratings"].apply(
            lambda v: json.dumps(v, default=str) if isinstance(v, (list, dict)) else v
        )

    # Optional curated allowlist: restrict (and order) CSV columns when provided,
    # keeping the JSON output complete. Unknown columns are silently skipped.
    csv_columns = output_cfg.get("csv_columns")
    if csv_columns:
        ordered = [c for c in csv_columns if c in csv_df.columns]
        if ordered:
            csv_df = csv_df[ordered]

    csv_df.to_csv(csv_path, index=False)
    logger.info("Wrote %s rows to %s and %s", len(records), json_path, csv_path)
    return {"json": str(json_path), "csv": str(csv_path)}
