import os
from dataclasses import dataclass
@dataclass(frozen=True)
class Config:
    timeout:int=int(os.getenv("REQUEST_TIMEOUT_SECONDS","20"))
    user_agent:str=os.getenv("ENGINE_USER_AGENT","SII-shared-free-data/1.0")
    hf_base:str="https://huggingface.co/datasets/tejhq/indian-markets/resolve/main"
CONFIG=Config()
