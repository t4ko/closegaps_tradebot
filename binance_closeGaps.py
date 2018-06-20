import math
import time
import sys
import signal
from binance.client import Client
from binance.exceptions import BinanceAPIException

# Best used on a high CPU (2+ cores) + high Network (~10Gbps) server close to the api gateway


class Bot:

    def __init__(self):
        # Output parameters, debug is not useful to normal users
        self.debug = True
        self.verbose = True

        # Modify this according to your personal parameters
        # Keys are top secret, delete them from your account if ever published
        self.simulate = True
        self.play_with_gains = True
        self.api_key = "thereisakeyhere"
        self.secret_key = "thereisakeyherebutitsasecret"
        self.api_client = Client(self.api_key, self.secret_key)
        self.use_feecoin, self.feecoin = True, "BNB"
        # Fund rules are the limit you are allowing the bot to trade with, usd estimated with USDT
        self.fund_rules = {
            'BTC': {'no_touch_coins': 0, 'max_coins': 0.05, 'max_percent': 100},
            'ETH': {'no_touch_coins': 0, 'max_coins': 0, 'max_percent': 100},
            'BNB': {'no_touch_coins': 0, 'max_coins': 0, 'max_percent': 100},
            'USDT': {'no_touch_coins': 0, 'max_coins': 0, 'max_percent': 100},
        }
        self.time_limit = {  # Longest time allowed for api calls
            'get_book': 0.4,
            'get_prices': 1
        }
        self.gap_limit_percent = 0.0001  # Minimum path earnings default at 0.1%

        # Don't touch this unless you know what you are doing
        self.rate_limits, self.call_count, self.last_timestamp = 600, 0, 0  # Request limiter per minute, burst distribution
        self.trade_fee = 1 - 0.00015  # Deduced from bought asset
        if self.simulate:
            self.balances = {'BTC': 0.1, 'ETH': 1, 'BNB': 100, 'USDT': 1000}
        else:
            self.balances = {}
        self.liquidations, self.liquidation_limit = [], 0.005  # Max tolerable loss, fee not included
        self.prec, self.pairs, self.gains, self.min, self.path_blacklist = {}, {}, {}, {}, {}
        self.symbol_coins = {}
        self.paths, self.order_ids = [], ["", "", "", ""]
        self.last_min_calculation = 0  # Last time the minimum amount for a trade was calculated

        signal.signal(signal.SIGINT, self.exit_program)
        signal.signal(signal.SIGTERM, self.exit_program)
        self.program_running = True
        self.start_engine()

    def loop(self):
        while self.program_running:
            current_time = time.time()
            loop_wait = self.call_count * (60 / self.rate_limits) - (current_time - self.last_timestamp)
            self.call_count = 0
            if loop_wait > 0:
                self.log_verbose("Waiting %.6f seconds before next iteration" % loop_wait)
                time.sleep(loop_wait)
            self.last_timestamp = current_time

            self.log_debug("Starting new loop")
            # Refresh blacklist
            self.refresh_blacklist()
            # Get symbols sell price
            success = self.reload_prices()
            if not success:
                continue
            # Calculate path coefficients
            self.log_debug("get_paths_data")
            path_prices = self.get_paths_data()
            self.log_verbose("Found {} potential path".format(len(path_prices)))
            self.log_verbose("Gains since started = {}".format(self.gains))

            # Iterate over path in descending values
            for path in sorted(path_prices, key=lambda p: p["path_value"], reverse=True):
                path_value, coin1 = path["path_value"], path["coin1"]
                buy = [None, path["buy1"], path["buy2"], path["buy3"]]
                sym = [None, path["sym1"], path["sym2"],  path["sym3"]]
                unique_string = "%.8f%s%s%s" % (path_value, sym[1], sym[2], sym[3])
                if unique_string in self.path_blacklist:
                    self.log_debug("Skipping path of the blacklist")
                    continue
                self.log_verbose("Path val=%.8f;Buy(%r) %s;Buy(%r) %s;Buy(%r) %s" % (path_value, buy[1], sym[1], buy[2], sym[2], buy[3], sym[3]))
                # Get books details and check quantity
                orders = [None, [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]]
                error, success = False, 0

                # Call order books
                self.log_verbose("Fetching top orders from price list")
                for index in range(1, 4):
                    book = self.pairs[self.symbol_coins[sym[index]]["quoteAsset"]][self.symbol_coins[sym[index]]["baseAsset"]]
                    if buy[index]:
                        orders[index][0] = book["bidPrice"]
                        orders[index][1] = book["bidQty"]
                    else:
                        orders[index][0] = book["askPrice"]
                        orders[index][1] = book["askQty"]

                success = True

                # Skip the rest if an error happened
                if not success:
                    self.log_verbose("Order books could not be retrieved")
                    break
                else:
                    self.log_debug("Fetched all orders")

                # Calculate optimized quantity and price, factor in fees
                balance = self.balances[coin1]
                order1, order2, order3 = orders[1], orders[2], orders[3]
                self.calc_path(balance, order1, order2, order3, path)
                earned, spent = path["earned"], path["spent"]
                # Check if the path is worth walking
                gains = earned - spent
                if spent <= 0 or earned / spent < 1 + self.gap_limit_percent or not self.exceed_min(spent, sym[1], "total"):
                    self.log_verbose("Path is not profitable anymore : spend %.8f get %.8f" % (spent, earned))
                    self.path_blacklist["%.8f%s%s%s" % (path_value, sym[1], sym[2], sym[3])] = time.time()
                    break
                self.log_info("Spending %.8f to earn %.8f : %s >> %s >> %s " % (spent, earned, sym[1], sym[2], sym[3]))

                if not self.simulate:
                    self.program_running = False

                # Everything is okay close the path
                self.order_ids = ["", "", "", ""]
                success = self.execute_order(1, path)  # Execute trade 1 A-B
                if success:
                    success = self.execute_order(2, path)  # Execute trade 2 B-C
                    if success:
                        success = self.execute_order(3, path)  # Execute trade 3 C-B
                        if success:
                            # Update available balances and gains
                            if coin1 not in self.gains:
                                self.gains[coin1] = 0
                            if buy[3]:
                                self.gains[coin1] = self.gains[coin1] + gains
                                if self.play_with_gains:
                                    self.balances[coin1] = self.balances[coin1] + gains
                            else:
                                self.gains[coin1] = self.gains[coin1] + gains
                                if self.play_with_gains:
                                    self.balances[coin1] = self.balances[coin1] + gains
                            self.log_verbose("Gains since started = {}".format(self.gains))

                break

            # Liquidate stuck balances
            self.liquidate()
            # Wait if necessary
            if len(path_prices) < 1:
                self.log_debug("No path found, forced wait of 100ms")
                time.sleep(0.1)

        self.exit_message()

    # SERIOUS BUSINESS, TRIPLE CHECK !
    def execute_order(self, order_num, path, force_price=0.0, force_qty=0.0, is_liquidation=False):
        buy, symbol, quantity, price = path["buy%d" % order_num], path["sym%d" % order_num], path["qty%d" % order_num], path["price%d" % order_num]
        order_placed, order_executed, limit_reached, order_canceled = False, False, False, False
        if force_price > 0:
            price = force_price
        if force_qty > 0:
            quantity = force_qty
        if buy:
            deal_type = 'SELL'
        else:
            deal_type = 'BUY'
        if self.simulate:
            while not order_executed and not order_canceled:
                # Place order
                while not order_placed:
                    self.log_verbose("Trying to place %s order on %s : [Price: %.8f - Quantity: %.8f]" % (deal_type, symbol, price, quantity))
                    try:
                        if buy:
                            self.api_call("create_sell_order")
                            self.log_verbose("Successfully placed sell order")
                        else:
                            self.api_call("create_buy_order")
                            self.log_verbose("Successfully placed buy order")
                        order_placed = True
                    except BinanceAPIException as e:  # No use in simulation
                        if e.code is "NO_BALANCE":  # Previous order not or partially filled, only order 2 or 3
                            self.log_verbose("Previous order not filled, cancel it and liquidate")
                            if not is_liquidation:
                                self.feed_liquidator(order_num - 1, path)
                        else:
                            self.log_error("Error while placing %s order. Verifying if order has been placed" % deal_type)

                # Check order status
                if is_liquidation and self.simulate:  # Too shitty to calculate
                    return True
                ret, loaded_book = None, False
                while not loaded_book:
                    # Load books
                    success, ret = self.get_order_book(buy, symbol, limit=5)
                    # Check if loaded
                    if ret is not None and len(ret) > 0:
                        loaded_book = True
                    elif not is_liquidation:
                        self.feed_liquidator(order_num, path)
                book_price = float(ret.pop()[0])
                if (buy and book_price >= price) or (not buy and book_price <= price):
                    self.log_verbose("%s order filled : %.8f %s for %.8f " % (deal_type, quantity, symbol, price))
                    return True

                # Cancel order
                else:
                    order_canceled = False
                    while not order_canceled:
                        try:
                            self.log_verbose("Order not executed price is now %.8f, trying to cancel" % book_price)
                            order_canceled = True
                            self.log_verbose("Order canceled")
                        except:
                            self.log_verbose("Failed to cancel order, trying again")
            if not is_liquidation:
                self.feed_liquidator(order_num, path)
            return False

        # WARNING : Not a simulation anymore! Real money to be lost
        # Real money : cancel order to check execution, check dealt orders for remaining money, liquidate remaining
        else:
            # Place order (fill or kill + instant or cancel)
            # Check result
            # Continue or liquidate
            return False

    # def check_order(self, order_num, path):
    #     buy = path["buy%d" % order_num]
    #     symbol = path["sym%d" % order_num]
    #     quantity = path["qty%d" % order_num]
    #     price = path["price%d" % order_num]
    #     spent, earned = path["spent"], path["earned"]
    #     if buy:
    #         deal_type = 'SELL'
    #         precision_coin = path["coin%d" % order_num]
    #     else:
    #         deal_type = 'BUY'
    #         precision_coin = path["coin%d" % ((order_num % 3) + 1)]
    #     oid = self.order_ids[order_num]
    #     check_cancel = True
    #
    #     # Check order execution
    #     order_failed, book_is_valid = False, True
    #     while not order_failed:
    #         time.sleep(0.020)
    #         try:
    #             self.api_call("get_dealt_orders")
    #             ret = self.api_client.get_symbol_dealt_orders(symbol=symbol, order_type=deal_type, limit=20)
    #         except KucoinAPIException as e:
    #             self.log_error("Error {}: {}".format(e.status_code, e.response))
    #             time.sleep(0.05)  # Limit spam if failure
    #         # Check if order active
    #         dealt_amount = 0
    #         for order in ret["datas"]:
    #             if order["orderOid"] == oid:
    #                 dealt_amount = dealt_amount + order["amount"]
    #         # Check the book
    #         ret = None
    #         filled_rate = 100 * dealt_amount / quantity
    #         self.log_debug("Completed %.1f percent of order" % filled_rate)
    #         if filled_rate > 99.9:
    #             self.log_debug("Dealt threshold reached, order successfully executed")
    #             return True
    #         else:
    #             if book_is_valid:
    #                 # Load order book
    #                 try:
    #                     if deal_type == "SELL":
    #                         self.api_call("get_buy_orders")
    #                         ret = self.api_client.get_buy_orders(symbol, limit=5)
    #                     else:
    #                         self.api_call("get_sell_orders")
    #                         ret = self.api_client.get_sell_orders(symbol, limit=5)
    #                     if ret and len(ret) > 0:
    #                         book_line = ret.pop()
    #                         if not ((deal_type == "SELL" and book_line[0] >= price) or (
    #                                 deal_type == "BUY" and book_line[0] <= price)):
    #                             self.log_debug("No more order in the book to close")
    #                             book_is_valid = False
    #                     else:
    #                         self.log_debug("The book contains nothing")
    #                         book_is_valid = False
    #                 except KucoinAPIException as e:
    #                     self.log_error("Error {}: {}".format(e.status_code, e.response))
    #             else:
    #                 order_failed = True
    #
    #     # Try to cancel, if order is dealt it will not work
    #     while check_cancel:
    #         try:
    #             self.api_call("cancel_order")
    #             self.api_client.cancel_order(order_id=oid, order_type=deal_type)
    #             self.log_verbose("Order %d not dealt or partially, cancelled." % order_num)
    #             check_cancel = False
    #         except KucoinAPIException as e:
    #             if e.status_code != 404:
    #                 self.log_error("Error {}: {}".format(e.status_code, e.response))
    #                 time.sleep(0.1)  # Limit spam if failure
    #             else:
    #                 self.log_verbose("Order not found, already dealt or never created")
    #                 return True  # Order can't be cancelled, it has been dealt
    #
    #     # Handle failure
    #     # Fetch dealt orders
    #     loaded_orders = False
    #     ret = None
    #     while not loaded_orders:
    #         try:
    #             self.api_call("get_symbol_dealt_orders")
    #             ret = self.api_client.get_symbol_dealt_orders(symbol=symbol, order_type=deal_type, limit=20)
    #             loaded_orders = True
    #         except KucoinAPIException as e:
    #             self.log_error("Error {}: {}".format(e.status_code, e.response))
    #             time.sleep(0.1)
    #     # Get dealt balance for order
    #     dealt_amount = 0
    #     for deal in ret["datas"]:
    #         dealt_amount = self.apply_precision(dealt_amount + deal["amount"], precision_coin)
    #     spent_amount = self.apply_precision(dealt_amount * (2 - self.trade_fee), precision_coin)
    #     remaining = self.apply_precision("qty%d" % order_num - spent_amount, precision_coin)
    #     self.log_verbose("Cancelled order detail : %.8f dealt; %.8f remaining" % (dealt_amount, remaining))
    #
    #     # Refactor the path to match the quantities
    #     new_spent = self.apply_precision(spent * remaining / quantity, path["coin1"])
    #     new_earned = self.apply_precision(earned * remaining / quantity, path["coin1"])
    #     path["spent"], path["earned"] = new_spent, new_earned
    #     path["qty%d" % order_num] = remaining
    #
    #     # Create new path if order 1 or 2 partially dealt
    #     if order_num != 3 and dealt_amount > 0:
    #         if buy:
    #             new_precision = path["coin%d" % (order_num + 1)]
    #         else:
    #             new_precision = path["coin%d" % (((order_num + 1) % 3) + 1)]
    #         artificial_spent = self.apply_precision(spent * dealt_amount / quantity, path["coin1"])
    #         artificial_earned = self.apply_precision(earned * dealt_amount / quantity, path["coin1"])
    #         new_qty = self.apply_precision(path["qty%d" % order_num + 1] * dealt_amount / quantity, path[new_precision])
    #         if new_qty > self.min_amount:
    #             # Create a new path only for liquidation
    #             artificial_path = dict(path_value=path["path_value"], coin1=path["coin1"], coin2=path["coin2"],
    #                                    coin3=path["coin3"], buy1=path["buy1"], buy2=path["buy2"], buy3=path["buy3"],
    #                                    sym1=path["sym1"], sym2=path["sym2"], sym3=path["sym3"], spent=artificial_spent,
    #                                    earned=artificial_earned, qty1=path["qty1"], qty2=path["qty2"],
    #                                    qty3=path["qty3"], price1=path["price1"], price2=path["price2"],
    #                                    price3=path["price3"])
    #             # Mod quantity
    #             artificial_path["qty%d" % order_num + 1] = new_qty
    #             self.feed_liquidator(order_num + 1, artificial_path)
    #
    #     if order_num != 1:
    #         # Add to liquidation if it failed
    #         if remaining > self.min_amount:
    #             self.log_verbose("Order %d failed, add to liquidation : Qty %.8f; Price %.8f; Sym %s; Buy%s" %
    #                              (order_num, path["qty%d" % order_num], price, symbol, buy))
    #             self.feed_liquidator(order_num, path)
    #         else:
    #             self.log_verbose("Insufficient quantity remaining, no need to liquidate %s" % precision_coin)
    #     else:
    #         self.log_verbose("Order %d failed, no need to liquidate %s" % (order_num, path["coin1"]))
    #     return False

    def feed_liquidator(self, order_num, path, force_down=True):
        if order_num is 1:
            return
        coin1, spent = path["coin1"], path["spent"]
        buy, symbol, quantity, price = path["buy%d" % order_num], path["sym%d" % order_num], path["qty%d" % order_num], path["price%d" % order_num]
        self.log_verbose("Order %d failed, add to liquidation : Qty %.8f; Price %.8f; Sym %s; Buy%s" % (order_num, quantity, price, symbol, buy))
        self.liquidations.append({"force_down": force_down, "failed_sym": symbol, "max_loss": self.liquidation_limit, "spent": spent, "path": path})
        # Update available balances and gains
        if coin1 not in self.gains:
            self.gains[coin1] = 0
        self.balances[coin1] = self.balances[coin1] - spent
        self.gains[coin1] = self.gains[coin1] - spent
        self.log_debug("Temporary balances and gains : \n {} \n {}".format(self.balances, self.gains))

    def liquidate(self):
        if not self.simulate:
            return
        liquidated = []
        list_len, index = len(self.liquidations), 0
        while index < list_len:
            entry = self.liquidations[index]
            index = index + 1

            # Define the coins to deal with
            force_down, path, spent, max_loss, failed_sym = entry["force_down"], entry["path"], entry["spent"], entry["max_loss"], entry["failed_sym"]
            earned = path["earned"]
            if failed_sym is path["sym2"]:  # Attempt down ?
                if force_down:  # Trade down
                    order_num = 2
                    liq_sym = path["sym2"]
                    coin, coin1 = path["coin2"], path["coin3"]
                    buy = path["buy2"]
                    qty = path["qty2"]
                    if buy:
                        price = path["price2"] * spent / earned
                    else:
                        price = path["price2"] * earned / spent
                else:  # Trade up, default
                    order_num = 1
                    liq_sym = path["sym1"]
                    coin, coin1 = path["coin2"], path["coin1"]
                    buy = not path["buy1"]
                    qty = self.qty_crop(path["qty1"] * self.trade_fee, path["sym1"])
                    price = path["price1"]
            else:  # Trade down
                liq_sym = path["sym3"]
                order_num = 3
                coin, coin1 = path["coin3"], path["coin1"]
                buy = path["buy3"]
                qty = path["qty3"]
                if buy:
                    price = path["price3"] * spent / earned
                else:
                    price = path["price3"] * earned / spent
            self.log_debug("Initial values : price %.8f; qty %.8f; spent %.8f" % (price, qty, spent))

            # Define the symbol
            if coin in self.pairs and coin1 in self.pairs[coin]:
                symbol = self.pairs[coin][coin1]["symbol"]
            else:
                symbol = self.pairs[coin1][coin]["symbol"]

            self.log_debug("Trying to liquidate %s on symbol %s" % (coin, symbol))
            # Get books for coin to coin1
            success, book = self.get_order_book(buy, symbol, limit=20)
            if not success or book is None or len(book) < 1:
                self.log_error("Error during api call for liquidation of %s" % coin)
                continue

            # Check if prices and quantity matches
            total_price, qty_left = 0, qty
            limit_price, max_qty = 0.0, 0.0  # float(book[0][0]), float(book[0][1])
            if buy:
                while max_qty < qty and len(book) > 0:  # Consume orders until qty and price are good
                    order = book.pop()
                    order_price, order_qty = float(order[0]), float(order[1])
                    consumed_qty = min(qty_left, order_qty)
                    qty_left = qty_left - consumed_qty
                    total_price = total_price + consumed_qty * order_price
                    limit_price = order_price
                    max_qty = max_qty + order_qty
                self.log_debug("Calculated : qty %.8f; max_qty %.8f; price %.8f; limit_price %.8f" % (qty, max_qty, price, limit_price))
                if max_qty < qty or limit_price < price * (1 - max_loss):
                    if failed_sym is path["sym2"]:
                        self.log_verbose("Order 2 liquidation failed, changing trade order")
                        entry["force_down"] = not force_down
                    self.log_verbose("Couldn't liquidate sell, buy price is too low : %.8f at quantity %.8f" % (limit_price, max_qty))
                    continue  # Too low to sell back
            else:
                max_total, max_qty = price * qty, 0
                total_left = max_total
                min_qty = qty * (1 - max_loss)
                while total_left > 0 and len(book) > 0:  # Consume orders until qty and price are good
                    order = book.pop()
                    order_price, order_qty = float(order[0]), float(order[1])
                    consumed_qty = min(total_left / order_price, order_qty)
                    total_left = total_left - (consumed_qty * order_price)
                    limit_price = order_price
                    max_qty = max_qty + consumed_qty
                self.log_debug("Calculated : qty %.8f; min_qty %.8f; max_qty %.8f; limit_price %.8f; price %.8f" % (qty, min_qty, max_qty, limit_price, price))
                if total_left/max_total > 0.001 or max_qty < min_qty:
                    if failed_sym is path["sym2"]:
                        entry["force_down"] = not force_down
                    self.log_verbose("Couldn't liquidate buy, sell price is too high : %.8f at quantity %.8f" % (limit_price, min_qty))
                    continue  # Too expensive to buy back
                qty = self.qty_crop(max_qty, liq_sym)

            # Execute order
            success = self.execute_order(order_num, path, force_price=limit_price, force_qty=qty, is_liquidation=True)
            if not success:
                self.log_error("Error during order execution for liquidation of %s" % coin)
            else:
                self.log_verbose("Successfully liquidated %.8f %s for %.8f" % (qty, symbol, limit_price))
                liquidated.append(index - 1)
                if failed_sym is path["sym2"] and force_down:  # Add the third step to liquidation
                    path["earned"] = path["spent"]
                    if buy:
                        overspend_ratio = price / limit_price
                    else:
                        overspend_ratio = path["qty2"] / qty
                    path["qty3"] = self.qty_crop(path["qty3"] / overspend_ratio, path["sym3"])
                    new_max_loss = max_loss - (overspend_ratio - 1)
                    self.log_verbose("Liquidation completed downward on order2, adding order3 to liquidation")
                    self.liquidations.append({"force_down": False, "failed_sym": path["sym3"], "max_loss": new_max_loss, "spent": spent, "path": path})
                    list_len = list_len + 1
                else:
                    if buy:
                        total = total_price * self.trade_fee
                    else:
                        total = qty * self.trade_fee
                    self.log_verbose("Liquidation completed upward on order2, adding result (%.8f) to balance and gains" % total)
                    self.log_debug(self.balances[coin1], total, self.balances[coin1] + total)
                    self.log_debug(self.gains[coin1], total, self.gains[coin1] + total)
                    self.balances[coin1] = self.balances[coin1] + total
                    self.gains[coin1] = self.gains[coin1] + total

        for index in list(reversed(liquidated)):  # Remove solved liquidations
            self.log_debug("Removing liquidation entry : {}".format(self.liquidations[index]))
            del self.liquidations[index]

    def refresh_blacklist(self):
        for unique_string in list(self.path_blacklist.keys()):
            if self.path_blacklist[unique_string] < time.time() - 10:
                self.log_debug("Remove path from blacklist : %s" % unique_string)
                del self.path_blacklist[unique_string]

    def get_order_book(self, buy, sym, limit=5):
        start_get_book = time.time()
        book, success = [], False
        # Call the book
        try:
            self.api_call("get_order_book")
            ret = self.api_client.get_order_book(symbol=sym, limit=limit)
            if buy:
                book = sorted(ret["bids"], key=lambda o: float(o[0]))
            else:
                book = sorted(ret["asks"], key=lambda o: float(o[0]), reverse=True)
            success = True
        except BinanceAPIException as e:
            self.log_error(e.response)
            success = False
            self.log_error("Failed to load the order book for %s" % sym)
        # Handle max allowed time
        if time.time() - start_get_book > self.time_limit['get_book']:
            success = False
            self.log_verbose("Book request took too long")
        return success, book

    def calc_path(self, balance1, order1, order2, order3, path):  # Data format : {balance, book1, book2, book3}
        self.log_debug("Calculating path prices and quantity")
        sym1, sym2, sym3 = path["sym1"], path["sym2"], path["sym3"]
        buy1, buy2, buy3 = path["buy1"], path["buy2"], path["buy3"]
        order1_qty, order2_qty, order3_qty = float(order1[1]), float(order2[1]), float(order3[1])
        order1_price, order2_price, order3_price = float(order1[0]), float(order2[0]), float(order3[0])

        # Down the path
        # Hop 1
        if buy1:
            qty = min(order1_qty, balance1)  # Max I can sell
            balance2 = (qty * order1_price)
        else:
            max_price = (order1_price * order1_qty)
            order_buy_price = min(max_price, balance1)  # Max I can spend
            balance2 = (order_buy_price / order1_price)
        if not self.use_feecoin:
            balance2 = balance2 * self.trade_fee
        self.log_debug("Down path : balance %.8f; Order %r; isBuy %s" % (balance1, order1, buy1))
        # Hop 2
        if buy2:
            qty = min(order2_qty, balance2)  # Max I can sell
            balance3 = (qty * order2_price)
        else:
            max_price = (order2_price * order2_qty)
            order_buy_price = min(max_price, balance2)  # Max I can spend
            balance3 = (order_buy_price / order2_price)
        if not self.use_feecoin:
            balance3 = balance3 * self.trade_fee
        self.log_debug("Down path : balance %.8f; Order %r; isBuy %s" % (balance2, order2, buy2))
        # Hop 3
        if buy3:
            qty = min(order3_qty, balance3)  # Max I can sell
            earned = (qty * order3_price)
        else:
            max_price = (order3_price * order3_qty)
            order_buy_price = min(max_price, balance3)  # Max I can spend
            earned = (order_buy_price / order3_price)
        if not self.use_feecoin:
            earned = earned * self.trade_fee
        self.log_debug("Down path : balance %.8f; Order %r; isBuy %s" % (balance3, order3, buy3))
        self.log_debug("Down path final balance : %.8f" % earned)

        # Up the path
        balance1, balance2, balance3 = earned, 0, 0
        # Hop 3
        if not self.use_feecoin:
            balance1 = balance1 / self.trade_fee
        if buy3:
            qty3 = self.qty_crop(balance1/order3_price, sym3)  # Qty sold
            balance3 = qty3
        else:
            qty3 = self.qty_crop(balance1, sym3)  # Qty bought
            balance3 = qty3 * order3_price
        self.log_debug("Up path : balance %.8f; Order %r; isBuy %s" % (balance1, order3, buy3))
        # Hop 2
        if not self.use_feecoin:
            balance3 = balance3 / self.trade_fee
        if buy2:
            qty2 = self.qty_crop(balance3/order2_price, sym2)  # Qty sold
            balance2 = qty2
        else:
            qty2 = self.qty_crop(balance3, sym2)  # Qty bought
            balance2 = qty2 * order2_price
        self.log_debug("Up path : balance %.8f; Order %r; isBuy %s" % (balance3, order2, buy2))
        # Hop 1
        if not self.use_feecoin:
            balance2 = balance2/self.trade_fee
        if buy1:
            qty1 = self.qty_crop(balance2/order1_price, sym1)  # Qty sold
            spent = qty1
        else:
            qty1 = self.qty_crop(balance2, sym1)  # Qty bought
            spent = qty1 * order1_price
        self.log_debug("Up path : balance %.8f; Order %r; isBuy %s" % (balance2, order1, buy1))
        self.log_debug("Up path final balance is %.8f" % spent)

        # Down the path again, adjusting precision
        self.log_debug("Adjusting precision and values")
        balance1, balance2, balance3 = spent, 0, 0
        # Hop 1
        if buy1:
            qty1 = self.qty_crop(balance1, sym1)  # Max I can sell
            balance2 = qty1 * order1_price
        else:
            qty1 = self.qty_crop(balance1 / order1_price, sym1)
            balance2 = qty1
        if not self.use_feecoin:
            balance2 = balance2 * self.trade_fee
        self.log_debug("Down path : balance %.8f; Order %r; isBuy %s" % (balance1, order1, buy1))
        # Hop 2
        if buy2:
            qty2 = self.qty_crop(balance2, sym2)  # Max I can sell
            balance3 = qty2 * order2_price
        else:
            qty2 = self.qty_crop(balance2 / order2_price, sym2)
            balance3 = qty2
        if not self.use_feecoin:
            balance3 = balance3 * self.trade_fee
        self.log_debug("Down path : balance %.8f; Order %r; isBuy %s" % (balance2, order2, buy2))
        # Hop 3
        if buy3:
            qty3 = self.qty_crop(balance3, sym3)  # Max I can sell
            earned = qty3 * order3_price
        else:
            qty3 = self.qty_crop(balance3 / order3_price, sym3)
            earned = qty3
        if not self.use_feecoin:
            earned = earned * self.trade_fee
        self.log_debug("Down path : balance %.8f; Order %r; isBuy %s" % (balance3, order3, buy3))
        self.log_debug("Down path final balance : %.8f" % earned)

        if self.use_feecoin:
            fee = spent * ((1 - self.trade_fee) ** 3)
            self.log_debug("Paying fees (%.8f) with %s" % (fee, self.feecoin))
            spent = spent + fee
        path["qty1"], path["qty2"], path["qty3"] = qty1, qty2, qty3
        path["price1"], path["price2"], path["price3"] = order1_price, order2_price, order3_price
        path["spent"], path["earned"] = spent, earned

    def get_paths_data(self):
        path_prices = []
        trade_fee = self.trade_fee ** 3
        pairs = self.pairs
        for path in self.paths:
            p0, p1, p2 = path[0], path[1], path[2]
            if p0 in self.balances and self.balances[p0] > 0:
                path_value, path_qty, initial_qty, buy1, buy2, buy3 = 1, 1, 0, False, False, False
                # First hop
                if p1 in pairs[p0]:
                    price = pairs[p0][p1]["askPrice"]
                    if price <= 0:
                        continue
                    path_value = 1 / price
                    initial_qty = min(price * pairs[p0][p1]["askQty"], self.balances[p0])
                    path_qty = initial_qty / price
                    sym1 = pairs[p0][p1]["symbol"]
                else:
                    price = pairs[p1][p0]["bidPrice"]
                    if price <= 0:
                        continue
                    initial_qty = max(pairs[p1][p0]["bidQty"], self.balances[p1])
                    path_qty = initial_qty * price
                    path_value = path_value * price

                    sym1 = pairs[p1][p0]["symbol"]
                    buy1 = True
                # Second hop
                if p1 in pairs and p2 in pairs[p1]:
                    price = pairs[p1][p2]["askPrice"]
                    if price <= 0:
                        continue
                    path_value = path_value / price
                    path_qty = min(path_qty, price * pairs[p1][p2]["askQty"]) / price
                    sym2 = pairs[p1][p2]["symbol"]
                else:
                    price = pairs[p2][p1]["bidPrice"]
                    if price <= 0:
                        continue
                    path_qty = max(pairs[p2][p1]["bidQty"], path_qty) * price
                    path_value = path_value * price
                    sym2 = pairs[p2][p1]["symbol"]
                    buy2 = True
                # Third hop
                if p2 in pairs and p0 in pairs[p2]:
                    price = pairs[p2][p0]["askPrice"]
                    if price <= 0:
                        continue
                    path_value = path_value / price
                    path_qty = min(path_qty, price * pairs[p2][p0]["askQty"]) / price
                    sym3 = pairs[p2][p0]["symbol"]
                else:
                    price = pairs[p0][p2]["bidPrice"]
                    if price <= 0:
                        continue
                    path_qty = max(pairs[p0][p2]["bidQty"], path_qty) * price
                    path_value = price * path_value
                    sym3 = pairs[p0][p2]["symbol"]
                    buy3 = True
                path_value = path_value * trade_fee
                if path_value - self.gap_limit_percent > 1:
                    path_prices.append({
                        "path_value": path_value, # - 1) * path_qty,
                        "coin1": p0,
                        "coin2": p1,
                        "coin3": p2,
                        "buy1": buy1,
                        "buy2": buy2,
                        "buy3": buy3,
                        "sym1": sym1,
                        "sym2": sym2,
                        "sym3": sym3
                    })
        self.log_debug(path_prices)
        return path_prices

    def price_crop(self, amount, symbol):
        return float(format(amount - (0.1 ** self.prec[symbol]["tickSize"]), '.%df' % self.prec[symbol]["tickSize"]))

    def qty_crop(self, amount, symbol):
        return float(format(amount - (0.1 ** self.prec[symbol]["stepSize"]), '.%df' % self.prec[symbol]["stepSize"]))

    def exceed_min(self, amount, symbol, min_type):
        if min_type == "price":
            return amount >= self.min[symbol]["minPrice"]
        elif min_type == "quantity":
            return amount >= self.min[symbol]["minQty"]
        elif min_type == "total":
            return amount >= self.min[symbol]["minTotal"]

    def reload_prices(self):
        self.log_debug("reload_prices")
        start_get_prices = time.time()
        req = None
        self.api_call("get_orderbook_ticker")
        req = self.api_client.get_orderbook_tickers()
        success = True
        if time.time() - start_get_prices > self.time_limit['get_prices']:  # Error if call too long
            success = False
        if success:
            pairs = self.pairs
            # prices = self.prices
            for symbol in req:
                sym = symbol["symbol"]
                coin_type, coin_type_pair = self.symbol_coins[sym]["baseAsset"], self.symbol_coins[sym]["quoteAsset"]
                if coin_type_pair in pairs and coin_type in pairs[coin_type_pair]:
                    askPrice, askQty = float(symbol["askPrice"]), float(symbol["askQty"])
                    bidPrice, bidQty = float(symbol["bidPrice"]), float(symbol["bidQty"])
                    if bidPrice == 0 or 1 - bidPrice/askPrice >= 0.1:
                        askPrice, bidPrice = 0, 0
                    pairs[coin_type_pair][coin_type]["askPrice"] = askPrice
                    pairs[coin_type_pair][coin_type]["askQty"] = askQty
                    pairs[coin_type_pair][coin_type]["bidPrice"] = bidPrice
                    pairs[coin_type_pair][coin_type]["bidQty"] = bidQty
            # self.log_debug(pairs)
        return success

    def start_engine(self):
        # Load list of pairs
        self.get_pairs_list()
        # Get list of coins
        self.get_coins_info()
        # Load funds available for trading based on balances, rules and volume
        self.get_avail_funds()
        self.log_info("Funds available for trading are : {}".format(self.balances))
        # load prices

        time.sleep(1)
        # Start analysis & trade loop
        self.loop()

    def get_pairs_list(self):
        self.api_call("get_exchange_info")
        req = self.api_client.get_exchange_info()

        # Add all pairs to the list
        for symbol in req["symbols"]:
            sym = symbol["symbol"]
            coin_type, coin_type_pair = symbol["baseAsset"], symbol["quoteAsset"]
            self.symbol_coins[sym] = {"baseAsset": coin_type, "quoteAsset": coin_type_pair}
            if symbol["status"] == "TRADING":
                if coin_type_pair not in self.pairs:
                    self.pairs[coin_type_pair] = {}
                self.pairs[coin_type_pair][coin_type] = {"bidPrice": 0, "bidQty": 0, "askPrice": 0, "askQty": 0, "symbol": sym}
                self.min[sym] = {}
                self.prec[sym] = {}
                for sym_filter in symbol["filters"]:
                    if "minPrice" in sym_filter:
                        self.min[sym]["minPrice"] = float(sym_filter["minPrice"])
                        self.prec[sym]["tickSize"] = int(abs(math.log10(float(sym_filter["tickSize"]))))
                    elif "minQty" in sym_filter:
                        self.min[sym]["minQty"] = float(sym_filter["minQty"])
                        self.prec[sym]["stepSize"] = int(abs(math.log10(float(sym_filter["stepSize"]))))
                    elif "minNotional" in sym_filter:
                        self.min[sym]["minTotal"] = float(sym_filter["minNotional"])
        # make a path list
        for QC1 in list(self.pairs.keys()):
            count = 0
            coin = QC1
            for AC1 in list(self.pairs[QC1].keys()):
                coin = AC1
                if AC1 in self.pairs:  # AC1 is a quote coin
                    for QC2 in list(self.pairs[AC1].keys()):
                        if QC2 in self.pairs[QC1] or (QC2 in self.pairs and QC1 in self.pairs[QC2]):
                            if [QC1, AC1, QC2] not in self.paths:
                                self.paths.append([QC1, AC1, QC2])
                            if [QC1, QC2, AC1] not in self.paths:
                                self.paths.append([QC1, QC2, AC1])
                            if [QC2, AC1, QC1] not in self.paths:
                                self.paths.append([QC2, AC1, QC1])
                            if [QC2, QC1, AC1] not in self.paths:
                                self.paths.append([QC2, QC1, AC1])
                            count = count + 1
                else:  # AC1 is not a quote coin
                    for QC2 in list(self.pairs.keys()):
                        if AC1 in self.pairs[QC2].keys():
                            if QC1 != QC2:
                                if QC1 in self.pairs[QC2] or QC2 in self.pairs[QC1]:
                                    if [QC1, AC1, QC2] not in self.paths:
                                        self.paths.append([QC1, AC1, QC2])
                                    if [QC1, QC2, AC1] not in self.paths:
                                        self.paths.append([QC1, QC2, AC1])
                                    if [QC2, AC1, QC1] not in self.paths:
                                        self.paths.append([QC2, AC1, QC1])
                                    if [QC2, QC1, AC1] not in self.paths:
                                        self.paths.append([QC2, QC1, AC1])
                                    count = count + 1
            if count == 0:
                del(self.pairs[QC1][coin])
        print(self.paths)
        # Count pairs
        pairs_count = 0
        for QC1 in self.pairs.keys():
            pairs_count = pairs_count + len(self.pairs[QC1])
        self.log_debug("All tradable pairs ({}) are : \n{}".format(pairs_count, self.pairs))

    def get_coins_info(self):
        self.api_call("returnCurrencies")
        #coins = self.api_client.return_currencies()
        coins = []
        for coin in coins:
            if coins[coin]["disabled"] is 0 and coins[coin]["delisted"] is 0 and coins[coin]["frozen"] is 0:
                self.tradePrecision[coin] = 8

    def get_avail_funds(self):  # No real money for now
        # Get account balance
        self.api_call("get_all_balances")
        all_balances = {}

        for coin in all_balances:
            free_balance = float(all_balances[coin])
            if free_balance > 0:
                self.balances[coin] = free_balance
        self.log_debug("All balances in account : {}".format(self.balances))

        # Apply manual rules
        for symbol in list(self.balances.keys()):
            # Remove
            if symbol not in self.fund_rules:
                del (self.balances[symbol])
                continue
            initial_balance = self.balances[symbol]
            # Remove no touch coins from balance
            if self.balances[symbol] > self.fund_rules[symbol]['no_touch_coins']:
                self.balances[symbol] = self.balances[symbol] - self.fund_rules[symbol]['no_touch_coins']
            else:
                del (self.balances[symbol])
            # limit the available balance to the max percentage allowed
            percent_limit = initial_balance * self.fund_rules[symbol]['max_percent'] / 100
            if self.balances[symbol] > percent_limit:
                self.balances[symbol] = percent_limit
                self.log_verbose(
                    "{} limited to {}% of total : {}".format(symbol, self.fund_rules[symbol]["max_percent"],
                                                             "%.2f" % self.balances[symbol]))
            # limit to the max of coins allowed
            if self.balances[symbol] > self.fund_rules[symbol]['max_coins']:
                self.balances[symbol] = self.fund_rules[symbol]['max_coins']
                self.log_verbose(
                    "{} balance exceed max_coins rule, limiting to {}{}".format(symbol, self.balances[symbol], symbol))

    def api_call(self, command):
        self.call_count = self.call_count + 1
        if self.call_count > 200:
            sys.exit()
        self.log_debug("API call : {} , counter: {}".format(command, self.call_count))

    def exit_message(self):
        self.log_info("Program interrupted, session stats :")
        self.log_info("Gains : \n{}".format(self.gains))
        self.log_verbose("Pending liquidations : \n{}".format(self.liquidations))
        self.log_verbose("Final trading balance (Depends on balance rules) : \n{}\n".format(self.balances))

    def exit_program(self, signum, frame):
        self.log_info("Interrupt signal received : SIG %d" % signum)
        self.program_running = False

    @staticmethod
    def log_error(*text):
        print('%.6f [ERR]' % time.time(), *text)

    @staticmethod
    def log_info(*text):
        print('%.6f [INF]' % time.time(), *text)

    def log_debug(self, *text):
        if self.debug:
            print('%.6f [DBG]' % time.time(), *text)

    def log_verbose(self, *text):
        if self.verbose:
            print('%.6f [VRB]' % time.time(), *text)


engine = Bot()
