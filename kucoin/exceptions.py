# coding=utf-8

import json


class KucoinAPIException(Exception):
    """Exception class to handle general API Exceptions

        `code` values

        `message` format

    """
    def __init__(self, request, response, status_code):
        self.code = ''
        self.message = 'Unknown Error'
        self.request = request
        try:
            json_res = json.loads(response)
        except ValueError:
            self.message = response
        else:
            if 'error' in json_res:
                self.message = json_res['error']
            if 'msg' in json_res:
                self.message = json_res['msg']
            if 'message' in json_res and json_res['message'] != 'No message available':
                self.message += ' - {}'.format(json_res['message'])
            if 'code' in json_res:
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
        return 'KucoinAPIException {}: {}\n Origin: {}'.format(self.code, self.message, self.request)


class KucoinRequestException(Exception):
    def __init__(self, message):
        self.message = message

    def __str__(self):
        return 'KucoinRequestException: {}\n Origin: {}'.format(self.message)


class KucoinResolutionException(Exception):
    def __init__(self, message):
        self.message = message

    def __str__(self):
        return 'KucoinResolutionException: {}'.format(self.message)
