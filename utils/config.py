import logging
import os
from dotenv import load_dotenv

# Load environment variables once
load_dotenv()

log = logging.getLogger(__name__)


def _role_id(name):
    # An empty or missing role ID becomes 0. guild.get_role(0) returns None,
    # so the features that use the role skip it instead of the cogs failing to load.
    value = (os.getenv(name) or "").strip()
    if not value:
        log.warning("%s is not set; features that use this role will skip it", name)
        return 0
    try:
        return int(value)
    except ValueError:
        log.warning("%s is not a number; treating it as unset", name)
        return 0


# Global Configuration Variables
ROLE_HOST_PLUS = _role_id('ROLE_HOST_PLUS')
ROLE_HOST = _role_id('ROLE_HOST')
ROLE_JAILED = _role_id('ROLE_JAILED')
ROLE_SPECTATOR = _role_id('ROLE_SPECTATOR')
ROLE_ARCHIVE = _role_id('ROLE_ARCHIVE')
