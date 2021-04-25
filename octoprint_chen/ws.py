# coding=utf-8
import time
import websocket
import logging
import threading

_logger = logging.getLogger('octoprint.plugins.chen')

class WebSocketClient:

	#可以将处理ws消息、ws错误、ws关闭的方法作为参数传入
    def __init__(self,url,on_ws_msg=None, on_ws_close=None, on_ws_open=None):
        self._mutex = threading.RLock()
        def on_error(ws, error):
            _logger.warn('ws error: {}'.format(error))
            self.close()
        def on_message(ws, msg):
            if on_ws_msg:
                on_ws_msg(ws, msg)
        def on_close(ws):
            _logger.debug('websocket closed')
            if on_ws_close:
                on_ws_close(ws)
        def on_open(ws):
            _logger.debug('websocket opened')
            if on_ws_open:
                on_ws_open(ws)

		#创建websocket.WebSocketApp类实例，建立ws连接
        self.ws = websocket.WebSocketApp(url,
                                  on_message = on_message,
                                  on_open = on_open,
                                  on_close = on_close,
                                  on_error = on_error,
        )

		#创建线程
        wst = threading.Thread(target=self.ws.run_forever)
        wst.daemon = True
        wst.start()
		#10s未连接则关闭ws
        for i in range(100):
            if self.connected():
                return
            time.sleep(0.1)
        self.ws.close()
        raise Exception('10s后仍未连接至websocket服务器！')

    def send(self, data, as_binary=False):
        with self._mutex:
            if self.connected():
                if as_binary:
                    self.ws.send(data, opcode=websocket.ABNF.OPCODE_BINARY)
                else:
                    self.ws.send(data)

    def connected(self):
        with self._mutex:
            return self.ws.sock and self.ws.sock.connected

    def close(self):
        with self._mutex:
            self.ws.keep_running = False
            self.ws.close()
