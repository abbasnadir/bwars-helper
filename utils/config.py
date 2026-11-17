import os

from dotenv import load_dotenv

# Load environment variables once
load_dotenv()

# Global Configuration Variables
ROLE_HOST_PLUS = int(os.getenv('ROLE_HOST_PLUS', '0'))
ROLE_HOST = int(os.getenv('ROLE_HOST', '0'))
ROLE_JAILED = int(os.getenv('ROLE_JAILED', '0'))
ROLE_SPECTATOR = int(os.getenv('ROLE_SPECTATOR', '0'))
ROLE_ARCHIVE = int(os.getenv('ROLE_ARCHIVE', '0'))
# Color roles are created directly above ROLE_COLOR_MIN and always stay below ROLE_COLOR_MAX.
ROLE_COLOR_MIN = int(os.getenv('ROLE_COLOR_MIN', '1202331251009130517'))
ROLE_COLOR_MAX = int(os.getenv('ROLE_COLOR_MAX', '1321589821118087210'))
