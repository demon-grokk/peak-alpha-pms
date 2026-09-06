# Known Bugs & Backlog — Peak Alpha PMS

## 1. Data Provider Anomalies (yfinance Web Feed Price Spikes)
- **Status**: Backlog / Logged
- **Description**:
  `yfinance` scrapes unverified web feeds from Yahoo Finance servers. On rare occasions (e.g., 2026-08-20 for `BALRAMCHIN.NS`), Yahoo Finance published an unadjusted/erroneous single-day closing price bar (`767.10` instead of actual NSE market price ~`727.70`).
- **Impact**:
  Erroneous price spikes in `yfinance` get written to `price_history`, creating false ATH price signals.
- **Proposed Solution**:
  1. Add a **Data Sanity Validator** in `src/ingestion/price_fetcher.py`:
     - Reject or flag single-day price jumps >15% that lack corresponding volume confirmation.
  2. Implement fallback price ingestion using official Dhan/ScanX price API or NSE official bhavcopy CSV downloads.


## 2. if Stock split/bonus happen how will i adjust the price_history in sqlite database and it's identification of such case otherwise our ATH price will be wrong..

## 3. 2.27M data in the sqlite price_history, is it neccessary?

## 4. 