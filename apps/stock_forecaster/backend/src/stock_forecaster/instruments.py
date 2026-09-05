from .market_data import normalize_ticker

INDEX_NAMES = {
  "000001.SS": "上证指数",
  "399001.SZ": "深证成指",
  "399006.SZ": "创业板指",
  "000300.SS": "沪深300",
  "000905.SS": "中证500",
  "000688.SS": "科创50",
}


def index_name(ticker: str) -> str | None:
  return INDEX_NAMES.get(normalize_ticker(ticker))