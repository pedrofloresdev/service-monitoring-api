from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "")
SECRET_KEY = os.getenv("SECRET_KEY", "thisisaverysecretkey123")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 60))
SCHEDULER_INTERVAL_SECONDS = int(os.getenv("SCHEDULER_INTERVAL_SECONDS", 60))
