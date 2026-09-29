def nse_year(year:int): return f"https://huggingface.co/datasets/tejhq/indian-markets/resolve/main/nse/year={year}/nse_{year}.parquet"
def nse_cross_section(as_of:str):
 import duckdb
 c=duckdb.connect(); c.execute("INSTALL httpfs; LOAD httpfs;")
 p=nse_year(int(as_of[:4])); q=f"SELECT symbol,isin,name,close,volume,turnover,date FROM read_parquet('{p}') WHERE date<=DATE '{as_of}' QUALIFY row_number() OVER(PARTITION BY coalesce(isin,symbol) ORDER BY date DESC)=1"
 return c.execute(q).fetchdf()
