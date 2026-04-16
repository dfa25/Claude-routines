# Existing content in scripts/daily_intercom_report.py
# ...

UK_VALUES = {"some_value"}
# ── Internal domains to exclude (substring match on domain part) ─────────────
INTERNAL_DOMAIN_SUBSTRINGS = {'avid'}

def is_internal_email(email):
    if not email or '@' not in email:
        return False
    domain = email.split('@')[-1].lower()
    return any(sub in domain for sub in INTERNAL_DOMAIN_SUBSTRINGS)

# API credentials
# ...
