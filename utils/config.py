import os
from dotenv import load_dotenv

# Load environment variables once
load_dotenv()

# Global Configuration Variables
ROLE_HOST_PLUS = int(os.getenv('ROLE_HOST_PLUS', 0))
ROLE_HOST = int(os.getenv('ROLE_HOST', 0))
ROLE_JAILED = int(os.getenv('ROLE_JAILED', 0))
ROLE_SPECTATOR = int(os.getenv('ROLE_SPECTATOR', 0))
ROLE_ARCHIVE = int(os.getenv('ROLE_ARCHIVE', 0))
