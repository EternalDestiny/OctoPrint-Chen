import logging
import time
import threading
from octoprint.filemanager.analysis import QueueEntry

_logger = logging.getLogger('octoprint.plugins.chen')

#两个作用：响应event和整理当前状态数据
class PrintEventTracker:
	def __init__(self):
		self._mutex = threading.RLock()

		#每个PrintEventTracker实例都可以绑定一个gcode打印任务，在打印开始时还可以记录开始时间
		self.current_print_ts = -1  #时间戳
		self.gcode_id= None


	def on_event(self, plugin, event, payload):
		with self._mutex:
			#当有文件开始打印，加上时间戳（本系统中服务器暂未利用这一数据，但可以以此标记打印的起始时间）
			if event == 'PrintStarted':
				self.current_print_ts = int(time.time())

		#见def octoprint_data
		data = self.octoprint_data(plugin)

		#添加event数据
		data['octoprint_event'] = {
			'event_type': event,
			'data': payload
		}

		# 打印失败时或完成后，取消绑定该打印任务（id和ts重置）
		with self._mutex:
			if event == 'PrintFailed' or event == 'PrintDone':
				self.current_print_ts = -1
				self.gcode_id= None
		return data


	#该方法获取当前的打印状态数据、文件分析数据等，没有event数据
	def octoprint_data(self, plugin, status_only=False):
		data = {
			'octoprint_data': plugin._printer.get_current_data()
		}


		with self._mutex:
			data['current_print_ts'] = self.current_print_ts
			if self.gcode_id:
				data['gcode_id'] = self.gcode_id

		#添加温度数据
		data['octoprint_data']['temperatures'] = plugin._printer.get_current_temperatures()

		#添加时间戳
		data['octoprint_data']['_ts'] = int(time.time())

		#只返回当前数据，不响应event
		if status_only:
			return data

		#当有文件时，获取文件分析数据
		data['octoprint_data']['file_metadata'] = self.get_file_metadata(plugin, data)

		return data

	def set_gcode_id(self, gcode_id):
		with self._mutex:
			self.gcode_id= gcode_id

	def get_gcode_id(self):
		with self._mutex:
			return self.gcode_id

	#获得gcode文件分析数据
	def get_file_metadata(self, plugin, data):
		try:
			current_file = data.get('octoprint_data', {}).get('job', {}).get('file', {})
			origin = current_file.get('origin')
			path = current_file.get('path')

			if not origin or not path:
				return None

			#调用octorprint提供的函数获得gcode文件分析数据
			file_metadata = plugin._file_manager._storage_managers.get(origin).get_metadata(path)

			return {'analysis': {
				'printingArea': file_metadata.get('analysis', {}).get('printingArea')}} if file_metadata else None
		except Exception as e:
			_logger.exception(e)
			return None
