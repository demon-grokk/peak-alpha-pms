"""
Strategy Orchestrator and Full Screener Engine for Peak Alpha PMS.
Combines Calendar-Clock Price Pipeline and Event-Clock Fundamentals Pipeline,
generating primary qualifying list and emerging momentum watchlist CSV exports with full metric details.
"""
import os
import pandas as pd
from typing import Dict, Any, List, Optional
from config.settings import EXPORTS_DIR
from src.db.database import db_manager
from src.runners.price_pipeline import PricePipelineRunner
from src.runners.fundamentals_pipeline import FundamentalsPipelineRunner
from src.filters.ath_price import ATHPriceFilter
from src.filters.relative_strength import RelativeStrengthFilter
from src.filters.ath_profit import ATHProfitFilter
from src.utils.logger import logger

class ScreenerEngine:
    def __init__(self):
        self.price_runner = PricePipelineRunner()
        self.fundamentals_runner = FundamentalsPipelineRunner()
        self.f1 = ATHPriceFilter()
        self.f2 = ATHProfitFilter()
        self.f3 = RelativeStrengthFilter()

    def run_full_screener(self, symbols: Optional[List[str]] = None, fetch_data: bool = True) -> Dict[str, Any]:
        """
        Executes end-to-end PMS screening pipeline and generates detailed CSV exports.
        """
        logger.info("==================================================")
        logger.info("       STARTING FULL PEAK ALPHA PMS SCREENER      ")
        logger.info("==================================================")

        # 1. Run Calendar Price Pipeline
        price_res = self.price_runner.run(symbols=symbols, fetch_latest_prices=fetch_data)

        # 2. Run Event Fundamentals Pipeline
        fund_res = self.fundamentals_runner.run(symbols=symbols, fetch_latest_fundamentals=fetch_data)

        from src.ingestion.price_fetcher import get_fetch_upper_bound
        effective_run_date = get_fetch_upper_bound().strftime("%Y-%m-%d")

        # 3. Query candidates from screen_results for effective_run_date
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT r.symbol, r.is_ath_price, r.is_emerging_candidate, r.outperforms_bse500, r.is_ath_profit, r.passes_all_filters
                FROM screen_results r
                WHERE r.run_date = ?;
            """, (effective_run_date,))
            rows = cursor.fetchall()

        candidate_rows = [r for r in rows if bool(r["passes_all_filters"]) or bool(r["is_emerging_candidate"])]
        candidate_syms = [r["symbol"] for r in candidate_rows]

        primary_records = []
        emerging_records = []

        if candidate_syms:
            # Bulk evaluate Filter 1 for candidate subset in 1 query
            f1_bulk = self.f1.evaluate_universe(symbols=candidate_syms)

            # Bulk fetch stock metadata for candidates in 500-symbol chunks
            stock_info_map = {}
            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                for i in range(0, len(candidate_syms), 500):
                    chunk = candidate_syms[i : i + 500]
                    placeholders = ",".join(["?"] * len(chunk))
                    cursor.execute(f"""
                        SELECT symbol, isin, name, sector, sub_sector, market_cap_category, bse_code, market_cap, pe_ratio, roce, roe
                        FROM stocks WHERE symbol IN ({placeholders});
                    """, chunk)
                    for s in cursor.fetchall():
                        stock_info_map[s["symbol"]] = s

            for r in candidate_rows:
                sym = r["symbol"]
                passes_all = bool(r["passes_all_filters"])
                is_emerging = bool(r["is_emerging_candidate"])

                f1_res = f1_bulk.get(sym) or self.f1.evaluate(sym)
                f2_res = self.f2.evaluate(sym)
                f3_res = self.f3.evaluate(sym)
                s_row = stock_info_map.get(sym)

                stk_data = {
                    "symbol": sym,
                    "name": s_row["name"] if s_row else None,
                    "sector": s_row["sector"] if s_row else None,
                    "sub_sector": s_row["sub_sector"] if s_row else None,
                    "market_cap_category": s_row["market_cap_category"] if s_row else None,
                    "bse_code": s_row["bse_code"] if s_row else None,
                    "isin": s_row["isin"] if s_row else None,
                    "current_adj_price": f1_res.metrics.get("current_adj_close"),
                    "ath_price": f1_res.metrics.get("ath_adj_close"),
                    "distance_from_ath_pct": round(f1_res.metrics.get("near_ath_price_pct") * 100, 2) if f1_res.metrics.get("near_ath_price_pct") is not None else None,
                    "current_ttm_pat_cr": round(f2_res.metrics.get("current_ttm_pat", 0) / 1e7, 2) if f2_res.metrics.get("current_ttm_pat") else None,
                    "ath_ttm_pat_cr": round(f2_res.metrics.get("ath_ttm_pat", 0) / 1e7, 2) if f2_res.metrics.get("ath_ttm_pat") else None,
                    "last_verified_ttm_pat": f2_res.metrics.get("as_of_quarter"),
                    "stock_52w_return_pct": round(f3_res.metrics.get("stock_52w_return", 0) * 100, 2) if f3_res.metrics.get("stock_52w_return") is not None else None,
                    "bse500_52w_return_pct": round(f3_res.metrics.get("bse500_52w_return", 0) * 100, 2) if f3_res.metrics.get("bse500_52w_return") is not None else None,
                    "relative_alpha_52w_pct": round(f3_res.metrics.get("relative_alpha_52w", 0) * 100, 2) if f3_res.metrics.get("relative_alpha_52w") is not None else None,
                    "roce_pct": s_row["roce"] if s_row else None,
                    "roe_pct": s_row["roe"] if s_row else None,
                    "pe_ratio": s_row["pe_ratio"] if s_row else None,
                    "market_cap_cr": s_row["market_cap"] if s_row else None,
                    "passes_all_filters": passes_all
                }

                if passes_all:
                    primary_records.append(stk_data)
                elif is_emerging:
                    emerging_records.append(stk_data)

        os.makedirs(EXPORTS_DIR, exist_ok=True)
        primary_export_path = os.path.join(EXPORTS_DIR, "primary_qualifying_stocks.csv")
        emerging_export_path = os.path.join(EXPORTS_DIR, "emerging_candidates.csv")

        df_primary = pd.DataFrame(primary_records)
        df_emerging = pd.DataFrame(emerging_records)

        df_primary.to_csv(primary_export_path, index=False)
        df_emerging.to_csv(emerging_export_path, index=False)

        primary_syms = [r["symbol"] for r in primary_records]
        emerging_syms = [r["symbol"] for r in emerging_records]

        def _fmt(syms: List[str]) -> str:
            if not syms:
                return "0 = []"
            return f"{len(syms)} = {syms if len(syms) <= 25 else syms[:25] + [...]}"

        logger.info("==================================================")
        logger.info("          SCREENING SUMMARY RESULTS               ")
        logger.info("==================================================")
        logger.info(f"Primary Qualifying Candidates (>=3yr history): {_fmt(primary_syms)}")
        logger.info(f"Emerging Momentum Candidates (1-3yr history): {_fmt(emerging_syms)}")
        logger.info(f"Primary Export Path: {primary_export_path}")
        logger.info(f"Emerging Export Path: {emerging_export_path}")

        return {
            "primary_count": len(primary_syms),
            "primary_syms": primary_syms,
            "emerging_count": len(emerging_syms),
            "emerging_syms": emerging_syms,
            "primary_export_path": primary_export_path,
            "emerging_export_path": emerging_export_path,
            "price_metrics": price_res,
            "fundamentals_metrics": fund_res
        }
