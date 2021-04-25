# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

import requests
import sys
import os
import time
import json
import logging
import threading
import requests
import queue

import octoprint.plugin
import octoprint.printer
import octoprint.filemanager

from .ws import WebSocketClient
from .print_event import PrintEventTracker

__python_version__ = 3 if sys.version_info >= (3, 0) else 2

_logger = logging.getLogger('octoprint.plugins.chen')

_print_event_tracker = PrintEventTracker()


class ChenPlugin(octoprint.plugin.SettingsPlugin,
				 octoprint.plugin.StartupPlugin,
				 octoprint.plugin.TemplatePlugin,
				 octoprint.plugin.ShutdownPlugin,
				 octoprint.plugin.EventHandlerPlugin,
				 ):

	def __init__(self):
		# 开发或测试用
		self.develop = False

		# 本地开发或服务器运行
		if self.develop:
			self.server_URL = 'http://127.0.0.1:8000'
			self.ws_url = 'ws://127.0.0.1:8000/ws/printer/'
		else:
			self.server_URL = 'http://49.234.134.71'
			self.ws_url = 'ws://49.234.134.71/ws/printer/'

		# webscoket连接实例
		self.wss = None

		# 传输给服务器的消息队列
		self.message_queue_to_server = queue.Queue(maxsize=1000)

		# gcode文件存放位置（插件启动后指定）
		self.g_code_folder=None

		# 直接给定printer_id
		self.printer_id = 'JNU_1'

	def on_shutdown(self):
		# 断开打印机连接
		self._printer.disconnect()

		# 有足够时间向服务器发送状态
		time.sleep(2)

		# 插件关闭后关闭websocket连接
		if self.wss is not None:
			self.wss.close()
			self.wss = None
		_logger.info('chen plugin 关闭')

	# ~~ Eventhandler mixin，当发生事件的时候自动调用
	def on_event(self, event, payload):
		global _print_event_tracker

		try:
			_logger.info('event: ' + event)
			# 在PrintStarted、PrintFailed、PrintDone时调用
			# PrinterStateChanged是连接打印机时引发的事件
			if event.startswith("Print"):
				event_payload = _print_event_tracker.on_event(self, event, payload)

				# 以上事件发生时，状态等数据加入数据传输队列
				if event_payload:
					self.message_queue_to_server.put_nowait(event_payload)
				else:
					self.message_queue_to_server.put_nowait(_print_event_tracker.octoprint_data(self))

		except Exception:
			_logger.warn('事件更新失败')

	# 插件启动时调用
	def on_after_startup(self):
		_logger.info("ChenPlugin Started!")

		# 自动在服务器上注册打印机，注册失败可以手动注册
		self.register_printer()

		# 指定gcode文件存放位置
		if not self._file_manager.folder_exists("local", path="gcode"):
			self._file_manager.add_folder("local", path="gcode", ignore_existing=False)
		self.g_code_folder = self._file_manager.path_on_disk("local", path="gcode")
		_logger.info("gcode located:" + self.g_code_folder)

		# 启动ws通信线程（后台不间断运行）
		ws_thread = threading.Thread(target=self.message_to_server_loop)
		ws_thread.daemon = True
		ws_thread.start()

	# ws通信线程
	def message_to_server_loop(self):
		def on_server_ws_close(ws):
			pass

		def on_server_ws_open(ws):
			_logger.info("websocket Opened")

		while True:
			time.sleep(1)

			if self._printer.get_current_connection()[0] == 'Closed':
				self._printer.connect()
				_logger.info(self._printer.get_current_connection())
			# 当有打印机连接时开始向服务器发送数据（每隔1秒，或者可以更低）
			else:
				try:

					# print(data)
					# 当没有websokcket连接或者连接失败时，持续尝试连接
					if not self.wss or not self.wss.connected():
						self.wss = WebSocketClient(self.ws_url,
												   on_ws_msg=self.process_server_msg,
												   on_ws_close=on_server_ws_close,
												   on_ws_open=on_server_ws_open)

					# 持续发送状态数据
					if self.message_queue_to_server.empty():
						data = _print_event_tracker.octoprint_data(self, status_only=True)
					else:
						data = self.message_queue_to_server.get()

					# 给出连接的printer_id
					data['printer_id'] = self.printer_id

					# 发送数据
					raw = json.dumps(data, default=str)
					self.wss.send(raw)

					_logger.info("ws:打印机数据推送成功")

				except Exception:
					_logger.warn("ws出错")

	# 处理服务器的数据，由三部分组成：message、command、data
	def process_server_msg(self, ws, raw_data):
		try:
			data = json.loads(raw_data)
			# print(data)

			# 服务器消息
			if data.get('message', {}):
				_logger.info('ws msg recieved:' + data['message'])

			# 服务器下发的指令
			if data.get('command', {}):
				cmd = data.get('command')

				# 服务器接收到打印机ready信息才会下发print指令
				if cmd == 'print':
					# data中包含gcode文件信息
					self.start_print(data.get('data'))

				# 以下暂时未在服务器实现
				if cmd == "pause":
					self._printer.pause_print()
				if cmd == 'cancel':
					self._printer.cancel_print()
				if cmd == 'resume':
					self._printer.resume_print()

		except:
			_logger.info("ws出错")

	# 响应print command
	def start_print(self, gcode_dict):
		global _print_event_tracker

		# 为当前打印任务加入gcode_id
		_print_event_tracker.set_gcode_id(gcode_dict.get('gcode_id'))

		# 获取gcode
		file_name = gcode_dict.get('gcode_name')
		file_path = os.path.join(self.g_code_folder, file_name)
		# print(self.file_path)
		try:
			r = requests.get(url=gcode_dict.get('gcode_url'))
			with open(file_path, "wb") as f:
				f.write(r.content)
			_logger.info("文件获取成功")
		except:
			_logger.info('文件获取失败')

		try:
			# 是否需要增加条件，如判断是否存在未取下的模型或材料（需要通过人工实现），当前默认ready则可以开始打印
			if True:
				self._printer.select_file(path=self.file_path, sd=False, printAfterSelect=False)
				self._printer.start_print()
				_logger.info("文件开始打印")

		except InvalidFileType:
			_logger.warn("文件非gcode文件")
		except InvalidFileLocation:
			_logger.warn("文件路径出错")
		except Exception:
			_logger.warn('打印失败')

	def register_printer(self):
		printer = {'printer_id': self.printer_id, 'owner': 'JNU', 'address': 'JNU'}

		try:
			r = requests.post(url=self.server_URL + '/regiser_printer_plugin/', data=printer)
			_logger.info('register_printer:' + r.text)
		except:
			_logger.warn('register failed')


__plugin_name__ = "chen"
__plugin_pythoncompat__ = ">=2.7,<4"
__plugin_implementation__ = ChenPlugin()
