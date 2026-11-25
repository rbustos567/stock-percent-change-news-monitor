# Stock Percent Change News Monitor

Python script that monitors watchlist tickers for percentage price moves on NYSE sessions, pulls recent news when gain/loss thresholds are crossed, and sends email alerts.

## Features

- **NYSE Session Validation:** Checks whether the target date is an active NYSE trading session using exchange_calendars (XNYS), skipping weekends and market holidays automatically.
- **Configurable Percentage Thresholds:** Loads defaults and per-ticker min_gain_pct / min_loss_pct rules from watchlist.json (with support for disabling either side via null).
- **Reference Date & Price Tracking:** Compares previous close against current/latest prices—handling multi-day gaps such as holiday weekends—and prints reference dates in each ticker header.
- **Recent News Lookup:** Fetches recent ticker news via yfinance within a configurable rolling lookback window (max_age_hours) and article cap (max_news) when a threshold triggers.
- **Simulation & Testing Flags:** Supports --as-of to simulate past NY timestamps and --force-threshold to test triggers with a uniform percentage cutoff.
- **SMTP Email Alerts:** Sends formatted alert emails via .env credentials (ALERT_EMAIL_FROM, ALERT_EMAIL_PASS, ALERT_EMAIL_TO) when --send-email is enabled and at least one trigger fires.
- **Structured Logging & cron Wrapper:** Includes configurable log levels (--log-level), optional log files (--log-file), and run_stocks_pct_news_trigger.sh with flock concurrency protection and automatic old-log cleanup.

## Repository Structure

```text
.
├── env.example
├── run_stocks_pct_news_trigger.sh
├── stocks_pct_news_trigger.py
├── watchlist.json
└── README.md
```

## Installation
1. Clone the repository:
```bash
   git clone https://github.com/rbustos567/stock-percent-change-news-monitor.git
   cd stock-percent-change-news-monitor
```
2. Install Python dependencies:
```bash
   python3 -m pip install --upgrade pip
   python3 -m pip install exchange_calendars yfinance pandas python-dotenv
```
3. Configure environment variables:
```bash
   cp env.example .env
   chmod 600 .env
```
Edit:
```bash
   ALERT_EMAIL_FROM="your_email@gmail.com"
   ALERT_EMAIL_PASS="your_app_password"
   ALERT_EMAIL_TO="recipient_email@gmail.com"
```
4. Configure your watchlist (watchlist.json):
Define default and per-ticker percentage thresholds (min_gain_pct, min_loss_pct), max_age_hours, and max_news.
5. Make the Bash wrapper executable:
```bash
   chmod +x run_stocks_pct_news_trigger.sh
```
## Usage

### Run directly with Python
```bash
# Standard run on an NYSE trading session
python3 stocks_pct_news_trigger.py --watchlist-config watchlist.json

# Send email alerts when percentage thresholds are triggered
python3 stocks_pct_news_trigger.py --watchlist-config watchlist.json --send-email

# Simulate a specific NY date/time and force a low threshold for testing
python3 stocks_pct_news_trigger.py --watchlist-config watchlist.json --as-of "2026-11-20 15:00" --force-threshold 0.1

# Enable verbose debug logging and write to a log file
python3 stocks_pct_news_trigger.py --watchlist-config watchlist.json --log-level DEBUG --log-file logs/trigger.log
```

### Run via the bash wrapper (cron -ready)
```bash
./run_stocks_pct_news_trigger.sh
```
To run every 30 minutes during US market hours on weekdays (Monday–Friday), add this line to crontab -e:
```bash
*/30 8-15 * * 1-5 /path/to/stock-percent-change-news-monitor/run_stocks_pct_news_trigger.sh
```

