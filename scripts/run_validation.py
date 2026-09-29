import json,os
from multibagger.validation import validate
months=os.getenv("VALIDATION_MONTHS","2022-03-31,2022-06-30,2022-09-30,2022-12-30,2023-03-31,2023-06-30,2023-09-29,2023-12-29,2024-03-28,2024-06-28,2024-09-30,2024-12-30,2025-03-31,2025-06-30,2025-09-30,2025-12-31").split(",")
os.makedirs("reports",exist_ok=True)
out=validate(months,top_n=20)
json.dump(out,open("reports/multibagger-pit-validation.json","w"),indent=2,default=str)
print(json.dumps(out,indent=2,default=str))
