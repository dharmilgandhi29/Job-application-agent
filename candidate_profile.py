# Back-compat shim: the profile now lives in config/user.json (single source of
# truth for user identity). This re-export keeps existing imports working.
from config.user import PROFILE as CANDIDATE_PROFILE
