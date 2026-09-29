# Schwab Trader API field notes

Practical notes from running a production integration against the Charles Schwab Trader API (the API that replaced the TD Ameritrade API after the migration). These are the things that broke for us in production, why, and what fixed them. They are not covered clearly in the official docs, and each one cost us real time or a real user's trade.

Maintained by the team behind [JorgAI](https://jorgai.com), an auto-trading platform where most connected accounts are Schwab. The long-form setup guide, written for traders rather than developers, is here: [How to automate trading in a Charles Schwab account](https://jorgai.com/blog/how-to-automate-trading-schwab-account).

Nothing here is affiliated with or endorsed by Charles Schwab. Verify against the current docs at [developer.schwab.com](https://developer.schwab.com/) before relying on any detail; Schwab changes things.

## Quick reference

| Item | Value | Notes |
|------|-------|-------|
| Auth | OAuth 2.0 authorization code | User signs in on schwab.com; you get `code`, exchange for tokens |
| Access token lifetime | 30 minutes | Every API call uses it |
| Refresh token lifetime | 7 days | Not extendable. User must re-authorize on schwab.com |
| Token endpoint | `https://api.schwabapi.com/v1/oauth/token` | Basic auth with `client_id:client_secret` |
| Authorize endpoint | `https://api.schwabapi.com/v1/oauth/authorize` | |
| Account identifier for orders | `hashValue` from `/trader/v1/accounts/accountNumbers` | Not the plain account number |
| Order placement | `POST /trader/v1/accounts/{hashValue}/orders` | Returns `201` with the order URL in the `Location` header, no body |
| Fractional shares | Not supported through the API | Whole-share quantities only |
| Bracket orders | `orderStrategyType: TRIGGER` with `childOrderStrategies` (`OCO` for two exits) | Works; see trap 3 |
| Day-trade counter | `roundTrips` on the securities account | Rolling 5 sessions |

## Trap 1: the refresh token dies every 7 days, and interval timers can kill the 30-minute one too

Two tokens, two clocks. The access token expires every 30 minutes. The refresh token, which mints new access tokens, expires 7 days after the user authorized. When it expires, the only fix is sending the user back through the OAuth sign-in. There is no API call that extends it.

What we got wrong was not the 7 days. It was the 30 minutes.

Our first version refreshed access tokens on an interval timer (every 20 minutes, refresh every connected account). Interval timers in most job schedulers reset when the worker restarts and only fire after a full period has elapsed. During a night of back-to-back deploys, the worker restarted often enough that the refresh never ran. Every access token on the platform went stale and every Schwab user was disconnected the next morning at once.

**What fixed it:** schedule the refresh on the wall clock (every quarter hour, cron-style), not on an interval. Wall-clock schedules survive restarts. See [`examples/refresh_schedule.py`](examples/refresh_schedule.py).

**Detecting the 7-day expiry:** the token endpoint returns `400` or `401` when the refresh token is dead or the user revoked access. Treat that as "needs re-authorization", notify the user once (deduplicate, because your refresh job will keep retrying every 15 minutes), and do not delete the stored connection; leaving it in place lets the rest of your system surface a clean "reconnect" state instead of a missing broker.

Refresh tokens may or may not rotate on refresh. Store the new one if it is present in the response; keep the old one if it is not.

## Trap 2: whole shares only, so dollar-based sizing silently produces zero-share orders

The Trader API does not accept fractional-share quantities. If your sizing logic works in dollars (most do), `$200 / $450 = 0.44 shares` must be rounded **down** before you build the order body. A fractional quantity is not rejected loudly in every case; we saw fractional orders accepted and never filled, which strands the user with an un-sellable position. Block fractional quantities on your side before they reach Schwab.

The user-facing consequence is the important part: a small per-trade budget on a whole-share broker means many candidates round down to zero shares. From the user's view this looks like "the scanner runs all day and never buys." Log the skip reason in plain language ("sizing covers less than 1 whole share") so the cause is visible, and consider a maximum-price-per-share filter so candidates the budget cannot reach are never scored in the first place.

A fractional position the user already holds at Schwab (from Stock Slices, dividend reinvestment, or a migrated TD account) cannot be sold through the API. It has to be closed in Schwab's own app.

## Trap 3: your own resting exit orders block your own liquidation, and the position data will not tell you

For an entry with both a take-profit and a stop-loss, the correct structure is a `TRIGGER` parent (the entry) with an `OCO` child holding a `LIMIT` sell and a `STOP` sell, both `GOOD_TILL_CANCEL`. Once the entry fills, the exits rest at Schwab and remain in force even if your servers are down. This is the right design.

The trap: those resting exit orders **reserve the shares**. If your system later tries to sell the full position for another reason (a daily loss brake, a manual close, a stale-position sweep), Schwab rejects the sell because the shares are committed to the resting bracket.

Some brokers report reserved shares (a `qty_available` lower than `qty`), so a "sell only what is available" check catches this. Schwab's position payload does **not**: `longQuantity` reports the full quantity even while your bracket holds it. A check that worked on another broker will never fire on Schwab, and the exit fails.

**What fixed it:** before any full liquidation, list your open orders for the symbol, cancel your own sell orders, wait a beat, re-read the position, then sell what is actually there. Do not trust the position quantity alone to tell you about your own resting orders. See [`examples/liquidate_with_resting_orders.py`](examples/liquidate_with_resting_orders.py).

## Smaller things worth knowing

- **Multiple accounts per login.** `/accountNumbers` returns a list. Decide explicitly which account you trade; do not assume one.
- **`Location` header, not a body.** A successful order `POST` returns `201` with no JSON. The new order id is the last path segment of the `Location` header. Fetch the order afterward to get its status.
- **Bracket rejection fallback.** If Schwab rejects a `TRIGGER`/`OCO` body for a given order, we fall back to a plain entry and manage exits from our side. Have that path ready rather than failing the entry.
- **Sessions.** We submit `session: NORMAL` (regular hours) only and run our scanner between 9:30 AM and 4:00 PM Eastern. We have not characterized extended-hours behavior and do not claim anything about it.
- **Numbers, not strings.** Send `quantity`, `price`, and `stopPrice` as JSON numbers.
- **Never ask for the password.** Anything that logs into schwab.com on the user's behalf violates the terms the user agreed to. OAuth is the only legitimate path, and users can revoke your app from Schwab's connected-apps settings at any time.

## Contributing

If you have hit something not listed here, open an issue with the request, the response, and the date. Corrections welcome; the point of this repo is to save the next person the week we lost.

## License

MIT. Use anything here however you like.
