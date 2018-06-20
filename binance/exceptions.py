# coding=utf-8
import json


class BinanceAPIException(Exception):

    LISTENKEY_NOT_EXIST = '-1125'

    def __init__(self, request, response, status_code):
        self.status_code = 0
        self.code = ''
        self.message = 'Unknown Error'
        self.request = request

        try:
            json_res = json.loads(response)
        except ValueError:
            self.message = 'Invalid JSON error message from Binance: {}'.format(response)
        else:
            self.message = json_res['msg']
            self.code = json_res['code']
            if 'data' in json_res:
                try:
                    self.message += " " + json.dumps(json_res['data'])
                except ValueError:
                    pass

        self.status_code = status_code
        self.response = response
        self.request = request

    def __str__(self):  # pragma: no cover
        return 'APIError(code=%s): %s' % (self.code, self.message)


class BinanceRequestException(Exception):
    def __init__(self, message):
        self.message = message

    def __str__(self):
        return 'BinanceRequestException: %s' % self.message


class BinanceOrderException(Exception):

    def __init__(self, code, message):
        self.code = code
        self.message = message

    def __str__(self):
        return 'BinanceOrderException(code=%s): %s' % (self.code, self.message)


class BinanceOrderMinAmountException(BinanceOrderException):

    def __init__(self, value):
        message = "Amount must be a multiple of %s" % value
        super(BinanceOrderMinAmountException, self).__init__(-1013, message)


class BinanceOrderMinPriceException(BinanceOrderException):

    def __init__(self, value):
        message = "Price must be at least %s" % value
        super(BinanceOrderMinPriceException, self).__init__(-1013, message)


class BinanceOrderMinTotalException(BinanceOrderException):

    def __init__(self, value):
        message = "Total must be at least %s" % value
        super(BinanceOrderMinTotalException, self).__init__(-1013, message)


class BinanceOrderUnknownSymbolException(BinanceOrderException):

    def __init__(self, value):
        message = "Unknown symbol %s" % value
        super(BinanceOrderUnknownSymbolException, self).__init__(-1013, message)


class BinanceOrderInactiveSymbolException(BinanceOrderException):

    def __init__(self, value):
        message = "Attempting to trade an inactive symbol %s" % value
        super(BinanceOrderInactiveSymbolException, self).__init__(-1013, message)


class BinanceWithdrawException(Exception):
    def __init__(self, message):
        if message == u'参数异常':
            message = 'Withdraw to this address through the website first'
        self.message = message

    def __str__(self):
        return 'BinanceWithdrawException: %s' % self.message
