"""Tests for options stock scanner modules."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

SCANNER_ROOT = Path(__file__).resolve().parents[1] / "scanner"
sys.path.insert(0, str(SCANNER_ROOT))

from scanner.config import load_config  # noqa: E402
from scanner.filters.dcf_model import _compute_wacc  # noqa: E402
from scanner.filters.strategy_matcher import match_strategy  # noqa: E402
from scanner.iv_cache import IVHistoryCache  # noqa: E402


def test_load_config_has_required_sections():
    cfg = load_config(SCANNER_ROOT / "config.yaml")
    assert "universe" in cfg
    assert "valuation" in cfg
    assert "options" in cfg
    assert "technical" in cfg
    assert "scoring" in cfg
    assert "weights" in cfg["scoring"]


def test_compute_wacc_reasonable_range():
    wacc = _compute_wacc(beta=1.1, debt_ratio=0.35, dcf_cfg={"risk_free_rate": 0.045, "market_return": 0.10})
    assert 0.06 <= wacc <= 0.20


def test_iv_cache_rank_and_percentile():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {
            "iv_cache": {
                "db_path": str(Path(tmp) / "iv.db"),
                "min_snapshots": 5,
                "snapshot_interval_hours": 0,
                "lookback_days": 365,
            }
        }
        cache = IVHistoryCache(cfg)
        code = "US.AAPL"
        for i, iv in enumerate([0.20, 0.22, 0.25, 0.28, 0.30, 0.35]):
            cache.record_iv(code, iv)

        metrics = cache.compute_iv_metrics(code, current_iv=0.32)
        assert metrics["iv_rank"] is not None
        assert metrics["iv_percentile"] is not None
        assert metrics["iv_rank"] > 50


@pytest.mark.parametrize(
    "row,expected_strategy_fragment",
    [
        (
            {
                "code": "US.AAPL",
                "valuation_score": 75,
                "technical_score": 65,
                "bullish_turnaround": True,
                "technicals_stabilizing": True,
                "iv_rank": 20,
            },
            "LEAPs",
        ),
        (
            {
                "code": "US.MSFT",
                "valuation_score": 70,
                "technical_score": 50,
                "bullish_turnaround": False,
                "technicals_stabilizing": True,
                "iv_rank": 75,
            },
            "Covered Calls",
        ),
        (
            {
                "code": "US.TSLA",
                "valuation_score": 60,
                "technical_score": 55,
                "bullish_turnaround": True,
                "technicals_stabilizing": True,
                "iv_rank": 45,
            },
            "Calendar Spreads",
        ),
    ],
)
def test_strategy_matcher(row, expected_strategy_fragment):
    cfg = load_config(SCANNER_ROOT / "config.yaml")
    rec = match_strategy(row, cfg)
    assert any(expected_strategy_fragment in s for s in rec.strategies)


def test_strategy_matcher_rejects_low_valuation():
    cfg = load_config(SCANNER_ROOT / "config.yaml")
    rec = match_strategy({"code": "US.X", "valuation_score": 25}, cfg)
    assert "Avoid" in rec.strategies[0]


def test_weighted_entry_score():
    from scanner.core import _entry_score  # noqa: E402

    cfg = load_config(SCANNER_ROOT / "config.yaml")
    row = {
        "valuation_score": 80,
        "technical_score": 70,
        "options_score": 60,
        "iv_setup_score": 50,
    }
    score = _entry_score(row, cfg)
    assert 65 <= score <= 75


def test_clamp_score():
    from scanner.scoring import clamp_score  # noqa: E402

    assert clamp_score(150) == 100.0
    assert clamp_score(-10) == 0.0


def test_parse_valuation_detail():
    from scanner.filters.pe_ps_filter import _parse_ratio_detail  # noqa: E402

    detail = _parse_ratio_detail(
        {
            "trend": {
                "current_value": 18.5,
                "average_value": 24.0,
                "forward_value": 16.0,
                "valuation_percentile": 22.0,
            },
            "market_distribution": {"median_value": 20.0, "ranking": 150},
            "plate_distribution": {
                "plate_average_value": 21.0,
                "plate_ranking": 12,
                "plate_name": "Technology",
            },
        }
    )
    assert detail["current"] == 18.5
    assert detail["percentile"] == 22.0
    assert detail["plate_avg"] == 21.0


def test_morningstar_score_from_fair_value():
    from scanner.filters.morningstar_research import _morningstar_score  # noqa: E402

    cfg = load_config(SCANNER_ROOT / "config.yaml")
    score, reason = _morningstar_score(100.0, 130.0, 4, 125.0, cfg)
    assert score > 50
    assert "fair value" in reason.lower() or "star" in reason.lower()


def test_serialize_analyst_rating_row():
    from scanner.filters.analyst_ratings import (  # noqa: E402
        _compute_analyst_score,
        _compute_summary_fields,
        serialize_analyst_row,
    )

    row = serialize_analyst_row(
        {
            "analyst_info": {
                "analyst_uid": "a1",
                "analyst_name": "Jane Doe",
                "analyst_picture_url": "http://example.com/pic.png",
                "num_of_stars": 4.5,
                "success_rate": 68.2,
                "stock_success_rate": 75.0,
                "stock_avg_return": 18.3,
                "update_time_str": "2026-06-12",
                "institution_info": {
                    "institution_name": "Goldman Sachs",
                    "institution_source_name": "TipRanks",
                    "institution_en_name": "Goldman Sachs",
                    "update_time_str": "2026-06-10",
                },
            },
            "rating_item_list": [
                {
                    "target_price": 220.0,
                    "rating": 4,
                    "recommendation_date_str": "2026-06-10",
                    "recommendation_date": 1781136000,
                    "rating_url": "https://example.com/report",
                    "analyst_uid": "a1",
                    "institution_uid": "i1",
                    "update_time_str": "2026-06-10",
                }
            ],
        }
    )
    assert "analyst_picture_url" not in str(row)
    assert row["analyst"]["name"] == "Jane Doe"
    assert row["broker"]["name"] == "Goldman Sachs"
    assert row["ratings"][0]["rating_label"] == "Buy"
    assert row["ratings"][0]["target_price"] == 220.0

    count, top_sr, median, weighted = _compute_summary_fields([row])
    assert count == 1
    assert top_sr == 75.0
    assert median == 220.0
    assert weighted == 220.0

    score, mos_pct, confidence, stale, spread, reason = _compute_analyst_score(
        200.0,
        220.0,
        75.0,
        [row],
        230.0,
        210.0,
        220.0,
        load_config(SCANNER_ROOT / "config.yaml"),
    )
    assert score > 0
    assert mos_pct is not None
    assert confidence == 75.0
    assert not stale
    assert reason


def test_is_eligible_analyst_filter():
    from scanner.filters.analyst_ratings import _is_eligible  # noqa: E402

    with_target = {
        "analyst": {"success_rate": 70.0},
        "ratings": [{"target_price": 200.0, "recommendation_date": 1781136000}],
    }
    no_target = {
        "analyst": {"success_rate": 70.0},
        "ratings": [{"rating": 4, "recommendation_date": 1781136000}],
    }
    no_sr = {
        "analyst": {},
        "ratings": [{"target_price": 200.0, "recommendation_date": 1781136000}],
    }

    assert _is_eligible(with_target, True, True)
    assert not _is_eligible(no_target, True, True)
    assert not _is_eligible(no_sr, True, True)
    # toggles off => everything passes
    assert _is_eligible(no_target, False, False)
    assert _is_eligible(no_sr, False, False)
    # success-rate guard only
    assert _is_eligible(no_target, False, True)
    assert not _is_eligible(no_sr, False, True)


def test_quality_multiplier_wide_moat():
    from scanner.filters.quality_multiplier import evaluate_quality_multiplier  # noqa: E402

    cfg = load_config(SCANNER_ROOT / "config.yaml")
    result = evaluate_quality_multiplier(
        {
            "economic_moat_label": "Wide",
            "uncertainty_label": "Low",
            "capital_allocation_label": "Exemplary",
        },
        cfg,
    )
    assert result.quality_factor > 1.0
    assert result.quality_moat_tier == "Wide"


def test_growth_context_value_trap():
    from scanner.filters.growth_context import evaluate_growth_context  # noqa: E402

    cfg = load_config(SCANNER_ROOT / "config.yaml")
    rel = {
        "pe_ttm": 12.0,
        "pe_forward": 18.0,
        "pe_percentile": 20.0,
    }
    detail = {
        "pe": {
            "profit_growth_rate": {
                "profit_data": [
                    {"finance_data_multiple": -0.15, "report_date_str": "2026-03-31"},
                    {"finance_data_multiple": 0.05, "report_date_str": "2025-12-31"},
                ]
            }
        }
    }
    result = evaluate_growth_context(rel, detail, cfg)
    assert result.growth_value_trap_flag
    assert result.growth_context_score < 40


def test_earnings_event_moves():
    from scanner.filters.earnings_reaction import _event_moves  # noqa: E402

    m1, m5 = _event_moves(
        [
            {"schedule_delta": 0, "schedule_close_price": 100.0},
            {"schedule_delta": 1, "schedule_close_price": 97.0},
            {"schedule_delta": 5, "schedule_close_price": 95.0},
        ]
    )
    assert m1 == pytest.approx(-3.0)
    assert m5 == pytest.approx(-5.0)


def test_valuation_score_breakdown():
    from scanner.valuation import _valuation_score  # noqa: E402

    cfg = load_config(SCANNER_ROOT / "config.yaml")
    score, breakdown = _valuation_score(
        relative_score=60,
        ms_score=50,
        intrinsic_score=70,
        analyst_score=65,
        growth_context_score=55,
        extended_multiples_score=50,
        earnings_risk_score=45,
        quality_factor=1.05,
        market_cap=5e11,
        min_mkt_cap=2e9,
        config=cfg,
        fundamental_score=60,
    )
    assert 0 <= score <= 100
    assert breakdown["quality_factor"] == 1.05
    assert "pillars" in breakdown
    assert "fundamental" in breakdown["pillars"]


def test_fundamental_pillar_weights_sum_to_one():
    cfg = load_config(SCANNER_ROOT / "config.yaml")
    v = cfg["valuation"]
    total = (
        v["relative_weight"]
        + v["morningstar_weight"]
        + v["intrinsic_weight"]
        + v["analyst_weight"]
        + v["growth_context_weight"]
        + v["extended_multiples_weight"]
        + v["earnings_risk_weight"]
        + v["fundamental_weight"]
    )
    assert abs(total - 1.0) < 1e-9


def test_pegy_ratio():
    from scanner.filters.extended_multiples import evaluate_extended_multiples  # noqa: E402

    cfg = load_config(SCANNER_ROOT / "config.yaml")
    fin = _make_financials(earnings_growth_pct=10.0)
    snap = {
        "total_market_val": 1e11,
        "last_price": 100.0,
        "dividend_ttm": 3.0,
    }
    rel = {"pe_ttm": 20.0}
    res = evaluate_extended_multiples(fin, snap, rel, cfg)
    assert res.peg_ratio == pytest.approx(2.0)  # 20 / 10
    assert res.pegy_ratio == pytest.approx(20 / 13, abs=0.01)  # 20 / (10 + 3% yield)
    assert res.extended_multiples["dividend_yield_used"] == pytest.approx(3.0)


def _make_financials(**kwargs):
    from scanner.filters.financials import FinancialsSummary  # noqa: E402

    base = dict(code="US.AAPL", data_source="financials")
    base.update(kwargs)
    return FinancialsSummary(**base)


def test_fundamental_fcf_yield_and_returns():
    from scanner.filters.fundamental_quality import evaluate_fundamental_quality  # noqa: E402

    cfg = load_config(SCANNER_ROOT / "config.yaml")
    fin = _make_financials(
        free_cash_flow_ttm=6e9,
        net_debt=0.0,
        roic=0.20,
        roe=0.25,
        net_debt_to_ebitda=0.5,
        debt_to_equity=0.4,
        fcf_trend="improving",
        revenue_trend="improving",
        margin_trend="stable",
    )
    snap = {"total_market_val": 1e11}
    res = evaluate_fundamental_quality(fin, snap, cfg)
    assert res.fcf_yield == pytest.approx(0.06, abs=1e-3)  # 6e9 / 1e11
    assert res.fundamental_score > 60
    assert res.leverage_flag is False


def test_fundamental_leverage_guardrail_caps_score():
    from scanner.filters.fundamental_quality import evaluate_fundamental_quality  # noqa: E402

    cfg = load_config(SCANNER_ROOT / "config.yaml")
    fin = _make_financials(
        free_cash_flow_ttm=5e9,
        net_debt=4e10,
        roic=0.15,
        net_debt_to_ebitda=5.0,  # distress (>4x)
        debt_to_equity=3.0,
        fcf_trend="stable",
        revenue_trend="stable",
        margin_trend="stable",
    )
    snap = {"total_market_val": 1e11}
    res = evaluate_fundamental_quality(fin, snap, cfg)
    assert res.leverage_flag is True
    assert res.fundamental_score <= 45.0


def test_series_trend_classification():
    from scanner.filters.financials import _series_trend  # noqa: E402

    improving = [("2026", 130.0), ("2025", 120.0), ("2024", 100.0), ("2023", 90.0)]
    deteriorating = [("2026", 80.0), ("2025", 90.0), ("2024", 110.0), ("2023", 120.0)]
    assert _series_trend(improving) == "improving"
    assert _series_trend(deteriorating) == "deteriorating"


def test_relative_volume():
    import pandas as pd

    from scanner.technicals import _relative_volume  # noqa: E402

    volumes = pd.Series([100.0] * 20 + [200.0])
    assert _relative_volume(volumes, 20) == pytest.approx(2.0)


def test_accumulation_ratio():
    import pandas as pd

    from scanner.technicals import _accumulation_ratio  # noqa: E402

    closes = pd.Series([10.0, 11.0, 10.5, 11.5, 12.0])
    volumes = pd.Series([100.0, 200.0, 150.0, 300.0, 250.0])
    ratio = _accumulation_ratio(closes, volumes, 4)
    assert ratio is not None
    assert ratio > 1.0


def test_volume_recovery_confirmed():
    import pandas as pd

    from scanner.technicals import _volume_recovery_confirmed  # noqa: E402

    n = 25
    closes = pd.Series([100.0] * (n - 2) + [99.0, 101.0])
    volumes = pd.Series([100.0] * (n - 1) + [250.0])
    assert _volume_recovery_confirmed(closes, volumes, 35.0, 32.0, 30.0, 3, 20)


def test_volume_score_bonus_high_rvol():
    from scanner.technicals import _volume_score_bonus  # noqa: E402

    cfg = {
        "enabled": True,
        "rvol_strong_threshold": 1.5,
        "rvol_confirm_threshold": 1.2,
        "rvol_weak_threshold": 0.5,
        "accumulation_min_ratio": 1.1,
        "min_adv_usd": 5_000_000,
    }
    bonus = _volume_score_bonus(1.6, 1.2, True, 10_000_000, cfg)
    assert bonus == pytest.approx(16.0)  # 8 rvol + 5 recovery + 3 accumulation


def test_config_has_volume_section():
    cfg = load_config(SCANNER_ROOT / "config.yaml")
    assert "volume" in cfg["technical"]
    assert cfg["technical"]["volume"]["enabled"] is True


def test_group_metrics_and_pct_above_price():
    from scanner.filters.analyst_ratings import (  # noqa: E402
        _group_metrics,
        _pct_targets_above_price,
    )

    entries = [
        {
            "analyst": {"success_rate": 60.0},
            "ratings": [{"target_price": 100.0, "recommendation_date_ts": 1700000000}],
        },
        {
            "analyst": {"success_rate": 80.0},
            "ratings": [{"target_price": 200.0, "recommendation_date_ts": 1700200000}],
        },
    ]

    m = _group_metrics(entries)
    assert m["n"] == 2
    assert m["tgt_min"] == 100.0 and m["tgt_max"] == 200.0 and m["tgt_avg"] == 150.0
    assert m["sr_min"] == 60.0 and m["sr_max"] == 80.0 and m["sr_avg"] == 70.0
    assert m["date_min"] and m["date_max"] and m["date_median"]
    assert m["date_min"] <= m["date_median"] <= m["date_max"]

    # empty group => zero count, null stats
    empty = _group_metrics([])
    assert empty["n"] == 0 and empty["tgt_avg"] is None

    assert _pct_targets_above_price(entries, 150.0) == 50.0
    assert _pct_targets_above_price(entries, None) is None


def test_to_dict_emits_aggregate_columns():
    from scanner.filters.analyst_ratings import AnalystRatingsResult  # noqa: E402

    flat = AnalystRatingsResult().to_dict()
    for prefix in ("recent", "topsr"):
        for suffix in ("n", "tgt_avg", "sr_avg", "date_median"):
            assert f"{prefix}_{suffix}" in flat
    assert "analyst_pct_above_price" in flat


def test_csv_columns_allowlist(tmp_path):
    import json as _json

    import pandas as pd

    from scanner.core import export_results  # noqa: E402

    df = pd.DataFrame(
        [
            {
                "code": "US.AAA",
                "name": "AAA",
                "entry_score": 90,
                "price": 100,
                "morningstar_report": {"x": 1},
                "strategy_rationale": "buy calls",
            }
        ]
    )
    cfg = {
        "output": {
            "json_path": str(tmp_path / "o.json"),
            "csv_path": str(tmp_path / "o.csv"),
            "csv_columns": ["code", "entry_score", "price", "does_not_exist"],
        }
    }
    paths = export_results(df, cfg)

    header = Path(paths["csv"]).read_text().splitlines()[0].split(",")
    assert header == ["code", "entry_score", "price"]  # unknown column skipped

    data = _json.loads(Path(paths["json"]).read_text())
    assert "morningstar_report" in data[0]  # JSON keeps full detail
