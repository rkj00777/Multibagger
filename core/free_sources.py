FREE_SOURCES={"NSE":"https://www.nseindia.com","BSE":"https://www.bseindia.com","TEJHQ":"https://huggingface.co/datasets/tejhq/indian-markets"}
PAID_PROVIDERS={"Bloomberg","Refinitiv","LSEG","Capital IQ","FactSet","TrueData","QntAify","Altys","Fincrux"}
def assert_free_only(provider:str):
    if provider in PAID_PROVIDERS: raise RuntimeError(f"Paid provider blocked: {provider}")
