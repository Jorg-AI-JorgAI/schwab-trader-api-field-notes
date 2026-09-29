"""Sell a whole position on Schwab when your own bracket is resting.

The lesson (see README, trap 3): resting exit orders (the OCO stop and
take-profit you attached to the entry) reserve the shares. A full-quantity
sell is rejected while they rest, and Schwab's position payload reports the
FULL quantity as held, so an "only sell what is available" check that works
on other brokers never fires here. Cancel your own sell orders first, wait a
beat, re-read the position, then sell what is really there.

Illustrative shape only; adapt to your own broker client.
"""
import asyncio


async def liquidate(broker, symbol: str, reason: str):
    # 1. Find OUR resting sell orders on this symbol. This is the
    #    authoritative signal; do not rely on available-quantity fields.
    resting = [
        o for o in await broker.get_open_orders()
        if o.symbol == symbol and o.side.lower() == "sell"
    ]

    # 2. Cancel them. Track whether anything was actually freed.
    freed = False
    for o in resting:
        try:
            if await broker.cancel_order(o.broker_order_id):
                freed = True
        except Exception as e:  # log and keep going; one failed cancel should not stop the exit
            print(f"cancel {o.broker_order_id} failed: {e}")

    # 3. Give the broker a moment to release the shares, then re-read the
    #    position so the sell uses real numbers rather than the pre-cancel view.
    if freed:
        await asyncio.sleep(1.5)

    pos = await broker.get_position(symbol)
    if pos is None or pos.qty <= 0:
        return None

    # 4. Whole shares only on Schwab. If a fractional remainder exists it came
    #    from outside the API (Stock Slices, DRIP) and cannot be sold here.
    qty = int(pos.qty)
    if qty < 1:
        print(f"{symbol}: fractional remainder {pos.qty} must be closed in the Schwab app")
        return None

    return await broker.place_order(
        symbol=symbol, qty=qty, side="sell", order_type="market", time_in_force="day",
    )
