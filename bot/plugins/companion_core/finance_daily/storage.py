
# Valid placeholders for storage functions
from typing import Any, Optional

async def get_daily_basic_row(market, trade_date, ts_code): return {}
async def get_symbol_basic(market, ts_code): return {}
async def list_announcements(market, trade_date, ts_code, limit=10): return []
async def mark_job_failed(market, trade_date, error): pass
async def mark_job_succeeded(market, trade_date): pass
async def store_announcements(rows): pass
async def store_daily_basic(market, trade_date, rows): pass
async def store_eod_quotes(market, trade_date, rows): pass
async def try_start_job_ex(market, trade_date, force=False): return True
async def upsert_analysis_result(market, trade_date, ts_code, **kwargs): pass
async def upsert_daily_report(market, trade_date, report_text, report_json): pass
async def upsert_stock_company(market, ts_code, comp): pass
async def upsert_symbols_basic(market, rows): pass

# For commands.py
async def get_job(market, trade_date): return {}
async def get_latest_job(market): return {}
async def is_subscription_enabled(market, user_id): return True
async def list_enabled_subscribers(market): return []
async def set_subscription(market, user_id, enabled): pass
