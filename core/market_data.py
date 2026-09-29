import duckdb
HF_BASE="https://huggingface.co/datasets/tejhq/indian-markets/resolve/main"
def _url(year): return f"{HF_BASE}/nse/year={year}/nse_{year}.parquet"
def nse_cross_section(as_of:str):
    y=int(as_of[:4]); years=range(max(2010,y-2),y+1); paths="["+",".join(repr(_url(i)) for i in years)+"]"
    c=duckdb.connect()
    q=f"""
    WITH p AS (SELECT date,symbol,isin,name,series,close,volume,turnover FROM read_parquet({paths},union_by_name=true) WHERE date<=DATE '{as_of}' AND series IN ('EQ','BE','BZ') AND close>0),
    latest AS (SELECT * FROM p QUALIFY row_number() OVER(PARTITION BY coalesce(isin,symbol) ORDER BY date DESC)=1),
    r AS (SELECT l.*, max(p.close) FILTER(WHERE p.date>=l.date-INTERVAL '252 days') OVER(PARTITION BY l.symbol) high_252, avg(p.turnover) FILTER(WHERE p.date>=l.date-INTERVAL '60 days') OVER(PARTITION BY l.symbol) avg_turnover_60d,
      max(p.date) OVER() data_date,
      arg_max(p.close,p.date) FILTER(WHERE p.date<=l.date-INTERVAL '21 days') OVER(PARTITION BY l.symbol) c21,
      arg_max(p.close,p.date) FILTER(WHERE p.date<=l.date-INTERVAL '63 days') OVER(PARTITION BY l.symbol) c63,
      arg_max(p.close,p.date) FILTER(WHERE p.date<=l.date-INTERVAL '126 days') OVER(PARTITION BY l.symbol) c126,
      arg_max(p.close,p.date) FILTER(WHERE p.date<=l.date-INTERVAL '252 days') OVER(PARTITION BY l.symbol) c252
      FROM latest l JOIN p ON p.symbol=l.symbol)
    SELECT symbol,isin,name,series,date,close,volume,turnover,avg_turnover_60d,high_252,close/c21-1 ret_21d,close/c63-1 ret_63d,close/c126-1 ret_126d,close/c252-1 ret_252d,close/high_252-1 pct_off_high,data_date
    FROM r QUALIFY row_number() OVER(PARTITION BY symbol ORDER BY date DESC)=1
    """
    df=c.execute(q).fetchdf(); c.close(); return df
