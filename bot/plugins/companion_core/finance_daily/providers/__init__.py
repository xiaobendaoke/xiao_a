
# Placeholder for providers
from typing import Any

class BaseProvider:
    def __init__(self, token=None, proxy=None):
        self.name = "base"
    async def fetch_daily(self, trade_date): return []
    async def fetch_daily_basic(self, trade_date): return []
    async def fetch_stock_basic(self): return []
    async def fetch_anns_by_symbol(self, code, start, end): return []
    async def fetch_anns_by_date(self, trade_date): return []
    async def last_open_trade_date(self): from datetime import datetime; return datetime.now().strftime("%Y%m%d")
    async def fetch_stock_company(self, code): return {}
    
class TushareProvider(BaseProvider):
    def __init__(self, token): self.name = "tushare"

class EastmoneyProvider(BaseProvider):
    def __init__(self, proxy=None): self.name = "eastmoney"

class SinaProvider(BaseProvider):
    def __init__(self): self.name = "sina"
