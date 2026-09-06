"""
Command Line Interface (CLI) for Peak Alpha PMS.
Provides commands to ingest universe metadata, run price and fundamentals pipelines,
and export screening outputs.
"""
import argparse
import sys
from src.db.schema import init_db
from src.ingestion.universe import fetch_nifty_total_market_universe
from src.runners.price_pipeline import PricePipelineRunner
from src.runners.fundamentals_pipeline import FundamentalsPipelineRunner
from src.core.screener import ScreenerEngine
from src.core.manual_review import ManualReviewManager
from src.runners.allocation_runner import run_allocation_evaluation

def main():
    parser = argparse.ArgumentParser(description="Peak Alpha PMS Quantitative Discovery Engine CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available PMS commands")

    # Command 1: init-db
    subparsers.add_parser("init-db", help="Initialize SQLite database schema")

    # Command 2: ingest-universe
    subparsers.add_parser("ingest-universe", help="Fetch Nifty Total Market CSV and populate stocks table")

    # Command 3: run-price
    price_parser = subparsers.add_parser("run-price", help="Run calendar-clock price pipeline (Filters 1 & 3 Stage A)")
    price_parser.add_argument("--no-fetch", action="store_true", help="Skip fetching latest price history from yfinance")
    price_parser.add_argument("--symbols", nargs="+", help="Specific symbols to process (e.g. RELIANCE TCS)")

    # Command 4: run-fundamentals
    fund_parser = subparsers.add_parser("run-fundamentals", help="Run event-clock ScanX fundamentals pipeline (Filter 2)")
    fund_parser.add_argument("--no-fetch", action="store_true", help="Skip fetching latest fundamentals from ScanX API")
    fund_parser.add_argument("--symbols", nargs="+", help="Specific symbols to process")

    # Command 5: run-all / screen
    screen_parser = subparsers.add_parser("run-all", help="Run complete end-to-end PMS screener pipeline")
    screen_parser.add_argument("--no-fetch", action="store_true", help="Skip fetching external data")
    screen_parser.add_argument("--symbols", nargs="+", help="Specific symbols to process")

    # Command 6: export-reviews
    subparsers.add_parser("export-reviews", help="Export pending manual review stocks to Screener.in watchlist CSV")

    # Command 7: evaluate-allocation
    alloc_parser = subparsers.add_parser("evaluate-allocation", help="Evaluate Layer 3 Allocation & Stop-Loss Engine for candidates")
    alloc_parser.add_argument("--symbols", nargs="+", help="Specific symbols to evaluate (Manual Exploratory)")
    alloc_parser.add_argument("--capital", type=float, default=1000000.0, help="Total net equity capital C (default 1,000,000)")
    alloc_parser.add_argument("--cash", type=float, default=1000000.0, help="Available deployable cash (default 1,000,000)")

    # Command 8: reconcile-full-history
    rec_parser = subparsers.add_parser("reconcile-full-history", help="Run periodic full-history price series reconciliation across universe")
    rec_parser.add_argument("--symbols", nargs="+", help="Specific symbols to reconcile")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Initialize DB on every CLI run
    init_db()

    if args.command == "init-db":
        print("Database schema initialized successfully.")

    elif args.command == "ingest-universe":
        count = len(fetch_nifty_total_market_universe())
        print(f"Successfully ingested {count} stocks into universe.")

    elif args.command == "run-price":
        runner = PricePipelineRunner()
        res = runner.run(symbols=args.symbols, fetch_latest_prices=not args.no_fetch)
        print("Price Pipeline Execution Summary:")
        print(f"  - Total Evaluated: {res['total_evaluated']}")
        print(f"  - Passed Primary ATH Price (>=3yr): {res['ath_passed']} = {res['ath_passed_syms']}")
        print(f"  - Passed Emerging ATH Price (1-3yr): {res['emerging_passed']} = {res['emerging_passed_syms']}")
        print(f"  - Passed RS vs BSE 500: {res['rs_passed']} = {res['rs_passed_syms']}")
        print(f"  - Qualified Candidates (Filter 1 + Filter 3): {res['both_passed']} = {res['both_passed_syms']}")

    elif args.command == "run-fundamentals":
        runner = FundamentalsPipelineRunner()
        res = runner.run(symbols=args.symbols, fetch_latest_fundamentals=not args.no_fetch)
        print("Fundamentals Pipeline Execution Summary:")
        print(f"  - Qualified Candidates Evaluated: {res['total_evaluated']}")
        print(f"  - Passed Filter 2 (ATH TTM PAT): {res['profit_passed']} = {res['profit_passed_syms']}")
        print(f"  - Routed to Manual Review: {res['manual_reviews']} = {res['manual_reviews_syms']}")

    elif args.command == "run-all":
        engine = ScreenerEngine()
        res = engine.run_full_screener(symbols=args.symbols, fetch_data=not args.no_fetch)
        print("\nFull Screener Execution Completed!")
        print(f"  - Primary Candidates (>=3yr): {res['primary_count']} = {res['primary_syms']}")
        print(f"  - Emerging Candidates (1-3yr): {res['emerging_count']} = {res['emerging_syms']}")
        print(f"  - Primary Export CSV: {res['primary_export_path']}")
        print(f"  - Emerging Export CSV: {res['emerging_export_path']}")

    elif args.command == "export-reviews":
        path = ManualReviewManager.export_screener_watchlist()
        print(f"Exported manual review watchlist to: {path}")

    elif args.command == "evaluate-allocation":
        res = run_allocation_evaluation(symbols=args.symbols, capital_c=args.capital, available_cash=args.cash)
        print("\nLayer 3 Allocation Evaluation Summary:")
        print(f"  - Total Evaluated: {res['total_evaluated']}")
        print(f"  - Approved: {res['approved_count']}")
        print(f"  - Rejected (>8% Risk): {res['rejected_risk_count']}")
        print(f"  - Insufficient Cash: {res['insufficient_cash_count']}")
        print(f"  - Remaining Cash: ₹{res['remaining_cash']:,.2f}")

    elif args.command == "reconcile-full-history":
        from src.ingestion.reconciliation import reconcile_full_price_history
        res = reconcile_full_price_history(symbols=args.symbols)
        print("\nFull-History Reconciliation Execution Summary:")
        print(f"  - Total Evaluated: {res['total_evaluated']}")
        print(f"  - Verified Clean / Unchanged: {res['clean_count']}")
        print(f"  - Reconciled & Re-written (Uniform): {res['refetched_count']}")
        print(f"  - Flagged Anomaly / Quarantined: {res['flagged_anomaly_count']} = {res['flagged_symbols']}")

if __name__ == "__main__":
    main()


