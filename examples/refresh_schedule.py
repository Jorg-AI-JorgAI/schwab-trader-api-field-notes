"""Keep Schwab access tokens alive without ever letting them lapse.

The lesson (see README, trap 1): schedule the refresh on the WALL CLOCK,
not on an interval. Interval timers reset on every worker restart and only
fire after a full period, so a night of back-to-back deploys can mean the
refresh never runs and every 30-minute access token on the platform goes
stale at once. Quarter-hour cron slots survive restarts.

This is a trimmed illustration. Storage, encryption, and your job runner
are up to you; the shape is what matters.
"""
import base64
import httpx

TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"


def refresh_one(client_id: str, client_secret: str, refresh_token: str) -> dict | None:
    """Exchange a refresh token for a new access token.

    Returns the token payload on success, or None when Schwab answers 400/401,
    which means the refresh token is dead (7-day limit reached) or the user
    revoked access. In that case the user has to sign in on schwab.com again;
    there is no API call that revives it.
    """
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    r = httpx.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=15,
    )
    if r.status_code in (400, 401):
        return None  # needs re-authorization; notify the user ONCE, keep the record
    r.raise_for_status()
    return r.json()


def refresh_all(accounts, client_id, client_secret, save, notify_reconnect):
    """Run this from a cron-style schedule such as `*/15 * * * *`.

    Access tokens last 30 minutes, so every 15 minutes leaves a margin even
    if one run is skipped. `save` persists rotated tokens; `notify_reconnect`
    should deduplicate (Redis key with a TTL, for example) because this job
    keeps running every 15 minutes after a token dies.
    """
    for acct in accounts:
        tokens = refresh_one(client_id, client_secret, acct.refresh_token)
        if tokens is None:
            notify_reconnect(acct)
            continue
        # The refresh token may or may not rotate. Keep the old one if the
        # response does not include a new one.
        save(
            acct,
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token") or acct.refresh_token,
        )


# Celery beat example of the wall-clock schedule (the fix):
#
#   from celery.schedules import crontab
#   beat_schedule = {
#       "refresh-schwab-tokens": {
#           "task": "workers.refresh_schwab_tokens",
#           "schedule": crontab(minute="*/15"),   # NOT timedelta(minutes=20)
#       },
#   }
