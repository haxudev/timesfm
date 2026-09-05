import sys

import pandas as pd

from stock_forecaster.instruments import index_name


def download_history(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
  import akshare as ak

  return ak.stock_zh_a_hist_tx(
    symbol=symbol,
    start_date=start_date,
    end_date=end_date,
    adjust="" if index_name(symbol) else "qfq",
    timeout=10,
  )


if __name__ == "__main__":
  print(download_history(*sys.argv[1:]).to_json(orient="split", date_format="iso"))