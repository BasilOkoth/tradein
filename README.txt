FULL REPLACEMENT FILES

Upload these files to BasilOkoth/tradein:

1. bot.html -> repository root /bot.html
2. engine.py -> /app/engine.py

The frontend REAL START hard-stop is removed.
The backend keeps an explicit one-purchase confirmation boundary for REAL mode.
Each REAL purchase, including recovery purchases, reaches WAITING_REAL_CONFIRMATION before BUY.
