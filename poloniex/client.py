import pycurl
import json
import time
import hmac, hashlib

from io import BytesIO


def create_time_stamp(datestr, format="%Y-%m-%d %H:%M:%S"):
    return time.mktime(time.strptime(datestr, format))


class poloniex:
    API_URL = 'https://54.95.147.104'
    API_VERSION = 'v1'

    def __init__(self, APIKey, Secret):
        self.APIKey = APIKey
        self.Secret = Secret
        self.session = self._init_session()

    def _init_session(self):
        session = pycurl.Curl()
        session.setopt(pycurl.SSL_VERIFYPEER, 0)
        session.setopt(pycurl.SSL_VERIFYHOST, 0)
        session.setopt(pycurl.TCP_NODELAY, 1)
        session.setopt(pycurl.TCP_KEEPALIVE, 1)
        return session

    def _order_params_for_sig(self, data):
        strs = []
        for key in sorted(data.keys()):
            strs.append("%s=%s" % (key, data[key]))
        return '&'.join(strs)

    def post_process(self, before):
        after = before

        # Add timestamps if there isnt one but is a datetime
        if 'return' in after and isinstance(after['return'], list):
                for x in xrange(0, len(after['return'])):
                    if isinstance(after['return'][x], dict):
                        if 'datetime' in after['return'][x] and 'timestamp' not in after['return'][x]:
                            after['return'][x]['timestamp'] = float(create_time_stamp(after['return'][x]['datetime']))

        return after

    def api_query(self, method, command, req={}):

        session = self.session
        # Prepare the basic headers
        headers = ['User-Agent: ']

        must_process = False
        req['command'] = command
        if method == "get":
            url = "https://poloniex.com/public"
            data_string = self._order_params_for_sig(req)
            if data_string != "":
                url = "%s?%s" % (url, data_string)
            session.setopt(pycurl.HTTPGET, True)

        else:
            must_process = True
            req['nonce'] = int(time.time() * 1000)
            data_string = self._order_params_for_sig(req)
            sign = hmac.new(self.Secret, data_string, hashlib.sha512).hexdigest()
            url = "https://poloniex.com/tradingApi"
            session.setopt(pycurl.POSTFIELDS, data_string)
            session.setopt(pycurl.POST, True)
            headers.append('Sign: %s' % sign)
            headers.append('Key: %s' % self.APIKey)

        # Apply the settings
        buff = BytesIO()
        session.setopt(pycurl.HTTPHEADER, headers)
        session.setopt(pycurl.URL, url)
        session.setopt(pycurl.WRITEDATA, buff)
        session.perform()

        # Handle the response
        json_ret = json.loads(buff.getvalue())
        if must_process:
            return self.post_process(json_ret)
        return json_ret

    def return_ticker(self):
        return self.api_query(
            "get",
            "returnTicker"
        )

    def return_24volume(self):
        return self.api_query(
            "get",
            "return24Volume"
        )

    def return_order_book(self, currency_pair, depth=1):
        return self.api_query(
            "get",
            "returnOrderBook",
            {
                'currency_pair': currency_pair,
                'depth': '%d' % depth
            }
        )

    def return_market_trade_history(self, currency_pair):
        return self.api_query(
            "get",
            "returnTradeHistory",
            {
                'currency_pair': currency_pair
            }
        )

    def return_chart_data(self, currency_pair, start, end):
        return self.api_query(
            "get",
            "returnChartData",
            {
                'currency_pair': currency_pair,
                'start': start,
                'end': end
            }
        )

    def return_currencies(self):
        return self.api_query("get", "returnCurrencies")

    def return_loan_orders(self):
        return self.api_query("get", "returnLoadOrders")

    # Returns all of your balances.
    # Outputs:
    # {"BTC":"0.59098578","LTC":"3.31117268", ... }
    def return_balances(self):
        return self.api_query(
            'post',
            'returnBalances'
        )

    # Returns your open orders for a given market, specified by the "currency_pair" POST parameter, e.g. "BTC_XCP"
    # Inputs:
    # currency_pair  The currency pair e.g. "BTC_XCP"
    # Outputs:
    # order_number   The order number
    # type          sell or buy
    # rate          Price the order is selling or buying at
    # Amount        Quantity of order
    # total         Total value of order (price * quantity)
    def return_open_orders(self, currency_pair):
        return self.api_query(
            'post',
            'returnOpenOrders',
            {
                "currency_pair": currency_pair
            }
        )

    # Returns your trade history for a given market, specified by the "currency_pair" POST parameter
    # Inputs:
    # currency_pair  The currency pair e.g. "BTC_XCP"
    # Outputs:
    # date          Date in the form: "2014-02-19 03:44:59"
    # rate          Price the order is selling or buying at
    # amount        Quantity of order
    # total         Total value of order (price * quantity)
    # type          sell or buy
    def return_trade_history(self, currency_pair):
        return self.api_query(
            'post',
            'returnTradeHistory',
            {
                "currency_pair": currency_pair
            }
        )

    # Places a buy order in a given market. Required POST parameters are "currency_pair", "rate", and "amount". If successful, the method will return the order number.
    # Inputs:
    # currency_pair  The currency pair
    # rate          price the order is buying at
    # amount        Amount of coins to buy
    # Outputs:
    # order_number   The order number
    def buy(self, currency_pair, rate, amount):
        return self.api_query(
            'post',
            'buy',
            {
                "currency_pair": currency_pair,
                "rate": rate,
                "amount": amount
            }
        )

    # Places a sell order in a given market. Required POST parameters are "currency_pair", "rate", and "amount". If successful, the method will return the order number.
    # Inputs:
    # currency_pair  The currency pair
    # rate          price the order is selling at
    # amount        Amount of coins to sell
    # Outputs:
    # order_number   The order number
    def sell(self, currency_pair, rate, amount):
        return self.api_query(
            'post',
            'sell',
            {
                "currency_pair": currency_pair,
                "rate": rate,
                "amount": amount
            }
        )

    # Cancels an order you have placed in a given market. Required POST parameters are "currency_pair" and "order_number".
    # Inputs:
    # currency_pair  The currency pair
    # order_number   The order number to cancel
    # Outputs:
    # succes        1 or 0
    def cancel(self, currency_pair, order_number):
        return self.api_query(
            'post',
            'cancelOrder',
            {
                "currency_pair": currency_pair,
                "order_number": order_number
            }
        )

    # Immediately places a withdrawal for a given currency, with no email confirmation. In order to use this method, the withdrawal privilege must be enabled for your API key. Required POST parameters are "currency", "amount", and "address". Sample output: {"response":"Withdrew 2398 NXT."}
    # Inputs:
    # currency      The currency to withdraw
    # amount        The amount of this coin to withdraw
    # address       The withdrawal address
    # Outputs:
    # response      Text containing message about the withdrawal
    def withdraw(self, currency, amount, address):
        return self.api_query(
            'post',
            'withdraw',
            {
                "currency": currency,
                "amount": amount,
                "address": address
            }
        )