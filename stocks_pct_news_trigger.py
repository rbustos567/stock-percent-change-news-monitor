import argparse
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import json
import os
from pathlib import Path
import smtplib
import time
from zoneinfo import ZoneInfo
import exchange_calendars as xcals
import pandas as pd
import yfinance as yf


def load_env_file(env_path: Path) -> None:
  """Load key=value pairs from a .env file into os.environ (without overriding existing env)."""
  if not env_path.exists():
    return

  try:
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=env_path, override=False)
    return
  except ImportError:
    pass

  for raw_line in env_path.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
      continue
    key, value = line.split("=", 1)
    key = key.strip()
    value = value.strip()
    # Strip matching surrounding quotes
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
      value = value[1:-1]
    os.environ.setdefault(key, value)


def parse_as_of(as_of_str: str | None) -> datetime:
  """Parse an optional NY timestamp string (YYYY-MM-DD or YYYY-MM-DD HH:MM)."""
  ny_tz = ZoneInfo("America/New_York")
  if not as_of_str:
    return datetime.now(ny_tz)

  for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
    try:
      dt = datetime.strptime(as_of_str, fmt)
      if fmt == "%Y-%m-%d":
        # Default to afternoon session time when only a date is supplied
        dt = dt.replace(hour=15, minute=0)
      return dt.replace(tzinfo=ny_tz)
    except ValueError:
      continue

  raise ValueError("Use format YYYY-MM-DD or 'YYYY-MM-DD HH:MM'")


def is_nyse_session(ny_dt: datetime) -> bool:
  """Return True if the given NY date is an active NYSE trading session."""
  xnys = xcals.get_calendar("XNYS")
  return bool(xnys.is_session(pd.Timestamp(ny_dt.date())))


def load_config(config_path: Path) -> dict:
  """Load and validate the watchlist JSON configuration."""
  data = json.loads(config_path.read_text(encoding="utf-8"))
  if not isinstance(data.get("tickers"), list) or not data["tickers"]:
    raise ValueError("Configuration JSON must contain a non-empty 'tickers' list.")
  return data


def evaluate_condition(
    pct_change: float, min_gain_pct: float | None, min_loss_pct: float | None
) -> tuple[str | None, str]:
  """
    Evaluate price change against gain and loss thresholds.
    Returns (trigger_type, human_readable_condition).
    """
  gain_target = abs(min_gain_pct) if min_gain_pct is not None else None
  loss_target = -abs(min_loss_pct) if min_loss_pct is not None else None

  if gain_target is not None and pct_change >= gain_target:
    return (
        "GAIN",
        f"GAIN >= +{gain_target:.2f}% (actual: {pct_change:+.2f}%)",
    )

  if loss_target is not None and pct_change <= loss_target:
    return (
        "LOSS",
        f"LOSS <= {loss_target:.2f}% (actual: {pct_change:+.2f}%)",
    )

  # Build summary of why no trigger fired
  parts = []
  if gain_target is not None:
    parts.append(f"need >= +{gain_target:.2f}%")
  else:
    parts.append("gain disabled")

  if loss_target is not None:
    parts.append(f"need <= {loss_target:.2f}%")
  else:
    parts.append("loss disabled")

  return None, f"NONE ({', '.join(parts)}; actual: {pct_change:+.2f}%)"


def fetch_price_change(symbol: str, use_last_close: bool) -> dict | None:
  """Fetch previous vs. current/latest close price, reference dates, and pct change."""
  ticker = yf.Ticker(symbol)

  if use_last_close:
    hist = ticker.history(period="5d", auto_adjust=False)
    if hist is None or hist.empty:
      return None
    closes = hist["Close"].dropna()
    if len(closes) < 2:
      return None

    prev_close = float(closes.iloc[-2])
    latest_price = float(closes.iloc[-1])
    prev_ref = closes.index[-2].strftime("%Y-%m-%d")
    latest_ref = closes.index[-1].strftime("%Y-%m-%d")
  else:
    info = ticker.fast_info
    prev_close = info.get("previousClose")
    latest_price = info.get("lastPrice")
    if not prev_close or not latest_price:
      return None

    prev_close = float(prev_close)
    latest_price = float(latest_price)

    # Resolve the exact prior trading session date from recent daily closes
    hist = ticker.history(period="5d", auto_adjust=False)
    closes = (
        hist["Close"].dropna()
        if hist is not None and not hist.empty
        else None
    )
    prev_ref = (
        closes.index[-2].strftime("%Y-%m-%d")
        if closes is not None and len(closes) >= 2
        else "prev_close"
    )
    latest_ref = "now"

  pct_change = ((latest_price - prev_close) / prev_close) * 100.0
  return {
      "prev_close": prev_close,
      "latest_price": latest_price,
      "pct_change": pct_change,
      "prev_ref": prev_ref,
      "latest_ref": latest_ref,
  }


def fetch_recent_news(
    symbol: str, ref_utc: datetime, max_age_hours: int = 24, limit: int = 3
) -> list[dict]:
  """Retrieve recent news items sorted newest-first within a rolling time window."""
  raw_news = yf.Ticker(symbol).news or []
  cutoff = ref_utc - timedelta(hours=max_age_hours)
  parsed_items = []

  for entry in raw_news:
    content = entry.get("content", {})
    pub_date_str = content.get("pubDate")
    if not pub_date_str:
      continue

    pub_dt = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))
    parsed_items.append((
        pub_dt,
        {
            "title": content.get("title", "Untitled"),
            "provider": (content.get("provider") or {}).get("displayName", ""),
            "local_time": pub_dt.astimezone().strftime("%Y-%m-%d %H:%M"),
            "url": (content.get("canonicalUrl") or {}).get("url", ""),
        },
    ))

  parsed_items.sort(key=lambda item: item[0], reverse=True)
  recent = [data for pub_dt, data in parsed_items if pub_dt >= cutoff]

  # Fall back to the newest available items if none fall inside the window
  return recent[:limit] if recent else [data for _, data in parsed_items[:limit]]


def send_alert_email(
    subject: str,
    body: str,
    smtp_host: str = "smtp.gmail.com",
    smtp_port: int = 587,
) -> None:
  """Send alert email using environment credentials."""
  sender = os.environ.get("ALERT_EMAIL_FROM", "").strip()
  password = os.environ.get("ALERT_EMAIL_PASS", "").strip()
  recipient = os.environ.get("ALERT_EMAIL_TO", "").strip()

  if not sender or not password or not recipient:
    raise ValueError(
        "Missing email env vars: ALERT_EMAIL_FROM, ALERT_EMAIL_PASS, or"
        " ALERT_EMAIL_TO."
    )

  msg = EmailMessage()
  msg["Subject"] = subject
  msg["From"] = sender
  msg["To"] = recipient
  msg.set_content(body)

  with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
    server.starttls()
    server.login(sender, password)
    server.send_message(msg)


def main() -> None:
  parser = argparse.ArgumentParser(
      description="Monitor ticker price changes from JSON and fetch news on trigger."
  )
  parser.add_argument(
      "--env-file",
      default=".env",
      help="Path to .env file (default: .env)",
  )
  parser.add_argument(
      "--watchlist-config",
      default="watchlist.json",
      help="Path to JSON watchlist config (default: watchlist.json)",
  )
  parser.add_argument(
      "--as-of",
      help="Simulate NY date/time, e.g. '2026-11-20 15:00' or '2026-11-20'",
  )
  parser.add_argument(
      "--force-threshold",
      type=float,
      default=None,
      help=(
          "Override gain/loss threshold with a single percentage value for testing"
          " (e.g. 0.1)"
      ),
  )
  parser.add_argument(
      "--send-email",
      action="store_true",
      help="Send triggered alerts via email using .env credentials.",
  )
  args = parser.parse_args()

  load_env_file(Path(args.env_file))

  config = load_config(Path(args.watchlist_config))
  defaults = config.get("defaults", {})

  ny_now = parse_as_of(args.as_of)
  if not is_nyse_session(ny_now):
    print(f"[{ny_now.strftime('%Y-%m-%d')}] Not an NYSE trading day/time. Exiting.")
    return

  # Use historical daily closes when simulating a past date
  use_last_close = args.as_of is not None
  ref_utc = ny_now.astimezone(timezone.utc)

  alert_blocks: list[str] = []

  for entry in config["tickers"]:
    symbol = entry["symbol"].upper()
    min_gain = (
        args.force_threshold
        if args.force_threshold is not None
        else entry.get("min_gain_pct", defaults.get("min_gain_pct"))
    )
    min_loss = (
        args.force_threshold
        if args.force_threshold is not None
        else entry.get("min_loss_pct", defaults.get("min_loss_pct"))
    )
    max_age_hours = int(
        entry.get("max_age_hours", defaults.get("max_age_hours", 24))
    )
    max_news = int(entry.get("max_news", defaults.get("max_news", 3)))

    try:
      price_data = fetch_price_change(symbol, use_last_close=use_last_close)
      if not price_data:
        print(f"{symbol:6s} [n/a]                      | Condition: NO_DATA")
        continue

      trigger_type, condition_msg = evaluate_condition(
          price_data["pct_change"], min_gain, min_loss
      )

      ticker_header = (
          f"{symbol:6s} [{price_data['prev_ref']} -> {price_data['latest_ref']}]"
      )
      summary_line = (
          f"{ticker_header:26s} | {price_data['prev_close']:.2f} -> "
          f"{price_data['latest_price']:.2f} | "
          f"Condition: {condition_msg}"
      )
      print(summary_line)

      if trigger_type:
        articles = fetch_recent_news(
            symbol,
            ref_utc=ref_utc,
            max_age_hours=max_age_hours,
            limit=max_news,
        )
        block_lines = [summary_line]
        for article in articles:
          source_tag = f" [{article['provider']}]" if article["provider"] else ""
          news_line = f"     * [{article['local_time']}]{source_tag} {article['title']}"
          print(news_line)
          block_lines.append(news_line)
          if article["url"]:
            url_line = f"       {article['url']}"
            print(url_line)
            block_lines.append(url_line)

        alert_blocks.append("\n".join(block_lines))
    except Exception as exc:
      print(f"{symbol:6s} [error]                  | Condition: ERROR | {exc}")

    # Small pause between requests to reduce rate-limit pressure
    time.sleep(0.3)

  if args.send_email:
    if not alert_blocks:
      print("No triggers fired; skipping email.")
      return

    subject = (
        f"Stock's Percentage Change ({ny_now.strftime('%Y-%m-%d %H:%M %Z')}) - "
        f"{len(alert_blocks)} triggered"
    )
    body = (
        "Triggered price threshold alerts and recent news:\n\n"
        + "\n\n".join(alert_blocks)
        + "\n"
    )
    try:
      send_alert_email(subject=subject, body=body)
      print("Alert email sent successfully.")
    except Exception as exc:
      print(f"Failed to send alert email: {exc}")


if __name__ == "__main__":
  main()
