import os
from dotenv import load_dotenv # type: ignore

load_dotenv()  # يقرأ محتويات ملف .env

BOT_TOKEN = os.getenv("BOT_TOKEN")          
GENERAL_CODE = os.getenv("GENERAL_CODE")   

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN غير موجود. ضعه في .env أو كمتغير بيئة.")
