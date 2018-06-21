# closegaps_tradebot
Experimental triangular arbitrage/high frequency crypto trading bot written with python 3

The direct use of this program is strongly disadvised.
The distributed code has not been updated in several months/year and the API call may be outdated.
The code is full of dirty debugging and arbitrary wait times, seriously, don't run it as is and don't reuse it unless you know your python.
I am sharing my code for people who wants to study or develop something similar and are looking for inspiration.

The API wrappers included are modded version of sammchardy's wrappers: 
https://github.com/sammchardy/python-kucoin
https://github.com/sammchardy/python-binance
The mods mainly consists in replacing the slow requests library by pycurl.

Experiments have been conducted to do triangular arbitrage on three different sites with this bot.
The exchanges tested were binance, kucoin and poloniex. The most advanced in this project being kucoin.
The low or lack of profitability due to the exchanges responses times being too long or inconsistent caused me to drop the project. 

All bots have a simulation mode enabled by default, change the 'simulate' variable to False to use real order placing API calls with real money.

Usage :
- Each file ending with "_closeGaps.py" is a standalone program, run it with python to use the bot 
- Edit the values of the api_key and secret_key to make api calls
- In simulation mode only "get" API calls are made such as getting the books, the balances, the rates, etc

Steps to profitability :
- Setup the bot on a high bandwidth server near the exchange you want to trade on
- Tune TCP to make it start faster (congestion control algorithm, initial window size, etc)
- Use pycurl, re-use https session, reduce information sent (user agent, compression, etc)
- Minimize the number of API calls 

Not tried :
- Setup different bots to get the books prices through public API calls and overcome the API limitation to have fresh information

Problems :
- At the time this was developped, kucoin order placing calls took more than 0.5 second which is 3 times too high to even approach profitability
- Some exchanges trade fees are so high there is very few opportunities, this could possibly be solved with lowered fees due to high trades volume
- There may be some undisclosed internal triangular arbitrage or just a lot of competition
- Python is slow, rewriting the program with a faster language could save some precious milliseconds.


