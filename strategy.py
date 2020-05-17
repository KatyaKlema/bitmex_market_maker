import bitmex
from bitmex_websocket import BitMEXWebsocket
import json
import logging
from time import sleep

class Strategy:
    def __init__(self, api_key, api_secret):
        self.api_key = api_key
        self.api_secret = api_secret
        self.client = bitmex.bitmex(api_key=api_key, api_secret=api_secret)
        self.orders = []

        self.isSellXBTM20 = False
        self.isBuyXBTM20 = False
        # initial conditions
        self.inventory = 100
        self.accumulated_sum = 0
        self.orders_XBTM20 = 50


    def place_order(self, order_info):
        temp_orders = []
        temp_orders.append(order_info)
        self.orders.append(order_info)
        self.client.Order.Order_newBulk(orders=json.dumps(temp_orders)).result()

    def is_limit_order(self, order_info):
        return 'price' in order_info

    def cancel_all_orders(self):
        # collect all market orders
        market_orders = []
        for order in self.orders:
            if not self.is_limit_order(order):
                market_orders.append(order)

        # cancellation of all orders
        self.client.Order.Order_cancelAll().result()

        # restore market orders
        for market_order in market_orders:
            self.place_order(market_order)

    def cancel_order(self, order_info):
        if self.is_limit_order(order_info):
            if not(order_info in self.orders):
                print("Error: Order was not placed before!")
                exit(0)

            # cancelation of all orders
            self.cancel_all_orders()

            # update our orders container
            self.orders.remove(order_info)

            # restoration of previously canceled orders
            self.client.Order.Order_newBulk(orders=json.dumps(self.orders)).result()


    def amend_order(self, old_order_info, new_order_info):
        if self.is_limit_order(old_order_info):
            self.cancel_order(old_order_info)
            self.place_order(new_order_info)

    def is_executed(self, order_info):
        symbol = order_info['symbol']
        side = order_info['side']
        price = None

        #check if the order is limit order
        if 'price' in order_info:
            price = order_info['price']

        # Instantiating the WS will make it connect. Be sure to add your api_key/api_secret.
        ws = BitMEXWebsocket(endpoint="https://testnet.bitmex.com/api/v1", symbol=symbol,
                             api_key=self.api_key,
                             api_secret=self.api_secret)

        for order in ws.market_depth():
            # checking existed orders in market glass for deal
            if side == 'Buy' and order['side'] == "Sell" and price == order['price'] and self.accumulated_sum >= price:
                return True
            elif side == 'Sell' and order['side'] == 'Buy' and price == order['price']:
                return True
        return False

    # this round is necessarily for tick size in bitmex
    @staticmethod
    def round(self, price):
        return int(price + 0.5)

    def algo_run(self):
        # get prices of futures with http or we can do it with websocket, now I chose http protocol
        XBTM20_price = self.client.Trade.Trade_get(symbol='XBTM20').result()[0][0]['price']
        XBTU20_price = self.client.Trade.Trade_get(symbol='XBTU20').result()[0][0]['price']

        # fixed middle spread
        mid_spred = (XBTU20_price - XBTM20_price) / 2

        # websocket for XBTM20
        ws_XBTM20 = BitMEXWebsocket(endpoint="https://testnet.bitmex.com/api/v1", symbol="XBTM20",
                             api_key=self.api_key,
                             api_secret=self.api_secret)

        # websocket for XBTU20
        ws_XBTU20 = BitMEXWebsocket(endpoint="https://testnet.bitmex.com/api/v1", symbol="XBTU20",
                             api_key=self.api_key,
                             api_secret=self.api_secret)

        while ws_XBTM20.ws.sock.connected and ws_XBTU20.ws.sock.connected:
            # finding the best bid price for XBTM20 in the market glass
            best_bid_XBTM20_price = 0
            depths = ws_XBTM20.market_depth()
            for depth in depths:
                if depth['side'] == 'Buy':
                    best_bid_XBTM20_price = max(depth['price'], best_bid_XBTM20_price)

            # finding the best ask price for XBTM20 in the market glass
            best_ask_XBTM20_price = 1e10
            for depth in depths:
                if depth['side'] == 'Sell':
                    best_ask_XBTM20_price = min(depth['price'], best_ask_XBTM20_price)

            # Если открытая позиция на XBTM20 >= Максимальный размер накопленной позиции,
            # перестать квотировать Limit Buy order. В противном случае квотировать Buy
            # сторону по условиям 100% времени.
            if self.orders_XBTM20 * self.inventory <= best_ask_XBTM20_price :
                # stop quoting the Limit Buy order
                self.isBuyXBTM20 = False
            else:
                # quoting the Limit Buy order
                self.isBuyXBTM20 = True

            # Если открытая позиция на XBTM20 <= -Максимальный размер накопленной позиции,
            # перестать квотировать Limit Sell order. В противном случае квотировать Sell
            # сторону по условиям 100% времени.
            if -self.orders_XBTM20 * self.inventory >= best_bid_XBTM20_price:
                # stop quoting the Limit Sell order
                self.isSellXBTM20 = False
            else:
                # quoting the Limit Sell order
                self.isSellXBTM20 = True

            if self.isSellXBTM20:
                # ------------ sell limit order XBTM20 --------------
                # finding best ask price for XBTU20 in market glass
                best_ask_XBTU20_price = 1e10
                for depth in ws_XBTU20.market_depth():
                    if depth['side'] == 'Sell':
                        best_ask_XBTU20_price = min(depth['price'], best_ask_XBTU20_price)

                # using following formula
                # best ask price XBTU20 - limit price XBTM20 = mid_spred - 5
                limit_price_XBTM20 = best_ask_XBTU20_price - mid_spred + 5
                sell_order_info_XBTM20 = {'symbol': 'XBTM20', 'price': round(limit_price_XBTM20), 'orderQty': 1, 'side': 'Sell'}
                self.place_order(sell_order_info_XBTM20)
                if self.is_executed(sell_order_info_XBTM20):
                    self.accumulated_sum += sell_order_info_XBTM20['price']
                    self.orders_XBTM20 -= 1
                # ---------------------------------------------------


            if self.isBuyXBTM20:
                # ------------ buy limit order XBTM20 --------------
                # finding best bid price for XBTU20 in market glass
                best_bid_XBTU20_price = 0
                for depth in ws_XBTU20.market_depth():
                    if depth['side'] == 'Buy':
                        best_bid_XBTU20_price = max(depth['price'], best_bid_XBTU20_price)

                # using following formula
                # best bid price XBTU20 - limit price XBTM20 = mid_spred - 5
                limit_price_XBTM20 = best_bid_XBTU20_price - mid_spred + 5
                buy_order_info_XBTM20 = {'symbol': 'XBTM20', 'price': round(limit_price_XBTM20), 'orderQty': 1, 'side': 'Buy'}
                self.place_order(buy_order_info_XBTM20)
                if self.is_executed(buy_order_info_XBTM20):
                    self.accumulated_sum -= buy_order_info_XBTM20['price']
                    self.orders_XBTM20 += 1
                # ---------------------------------------------------
            print("OK")
            sleep(10)


strategy = Strategy(api_key='YQoQFiu2QHAQDIj1ez1hNfVC', api_secret='WkxmhD9V2aIluExCEEIGlzgDiWRpVtFR17TwTOVTY42cu-L1')
strategy.algo_run()
