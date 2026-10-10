TRADEIN TOP-4 RECOVERY V6

Replace:
  /bot.html
  /app/top4_basket.py

Top-4 recovery:
- maximum 6 rounds
- target cycle profit: $1.00
- assumed DigitMatch total return: 8.93x
- minimum leg stake: $0.35
- next stake is calculated from actual settled Top-4 losses
- a profitable basket resets the cycle to Round 1
- after 6 consecutive losing baskets, recovery stops
- the next basket cannot open while the previous basket is settling
- backend owns the Top-4 recovery stake to avoid stale browser values

REAL:
- no unattended recovery purchases
- each REAL basket still requires one explicit confirmation
- after confirmation, fresh proposals are requested and the basket is submitted immediately
