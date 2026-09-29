import time,requests
from .config import CONFIG
class PublicHTTP:
 def __init__(self,referer=None):
  self.s=requests.Session(); self.s.headers.update({"User-Agent":CONFIG.user_agent,"Accept":"*/*"}); self.s.headers.update({"Referer":referer} if referer else {})
 def get(self,url,params=None,retries=3):
  last=None
  for i in range(retries):
   try:
    r=self.s.get(url,params=params,timeout=CONFIG.timeout)
    if r.ok and r.content:return {"status":"OK","content":r.content}
    last=f"HTTP {r.status_code}"
   except Exception as e:last=f"{type(e).__name__}: {e}"
   time.sleep(.75*(i+1))
  return {"status":"DATA_UNAVAILABLE","content":b"","detail":last}
