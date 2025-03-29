import os
import json
import requests
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QProgressBar, QFileDialog,
    QComboBox, QTextEdit, QListWidget, QMessageBox, QDialog, QTabWidget,
    QDoubleSpinBox, QAction  # <-- 加上 QAction
)
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QTimer, QSettings, QThread
from PyQt5.QtGui import QTextCursor, QFont, QIcon, QTextCharFormat, QColor
CONFIG_FILE = "config.ini"

def calculate_auto_max_tokens(prompt, context_limit=4096):
    # 粗略估算：每4个字符大约对应1个token
    estimated_tokens = len(prompt) // 4
    auto_max_tokens = context_limit - estimated_tokens
    if auto_max_tokens < 50:
        auto_max_tokens = 50
    return auto_max_tokens

class StreamWorker(QThread):
    """用于流式接收API响应的线程"""
    new_token = pyqtSignal(str)
    finished = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, api_url, api_key, model_name, prompt, temperature):
        super().__init__()
        self.api_url = api_url
        self.api_key = api_key
        self.model_name = model_name
        self.prompt = prompt
        self.temperature = temperature
        self._is_running = True

    def run(self):
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream"
            }
            
            # 自动计算 max_tokens
            max_tokens = calculate_auto_max_tokens(self.prompt)
            
            payload = {
                "model": self.model_name,
                "messages": [{"role": "user", "content": self.prompt}],
                "stream": True,
                "max_tokens": max_tokens,
                "temperature": self.temperature,
                "top_p": 0.8
            }

            with requests.post(
                self.api_url,
                json=payload,
                headers=headers,
                stream=True,
                timeout=60
            ) as response:
                if response.status_code != 200:
                    raise Exception(f"API请求失败: {response.status_code} - {response.text}")

                buffer = ""
                for chunk in response.iter_content(chunk_size=1024):
                    if not self._is_running:
                        break

                    chunk_str = chunk.decode('utf-8')
                    buffer += chunk_str
                    
                    # 处理SSE格式数据
                    while "\n\n" in buffer:
                        event, buffer = buffer.split("\n\n", 1)
                        if "data: " in event:
                            data = event.split("data: ")[1]
                            if data != "[DONE]":
                                try:
                                    json_data = json.loads(data)
                                    if "choices" in json_data and len(json_data["choices"]) > 0:
                                        delta = json_data["choices"][0].get("delta", {})
                                        if "content" in delta:
                                            self.new_token.emit(delta["content"])
                                except json.JSONDecodeError:
                                    continue

            self.finished.emit()

        except Exception as e:
            self.error_occurred.emit(str(e))

    def stop(self):
        self._is_running = False

class Worker(QObject):
    progress_updated = pyqtSignal(int)
    log_message = pyqtSignal(str)
    preview_request = pyqtSignal(str)
    preview_response = pyqtSignal(str, bool)  # 第二个参数表示是否为最终内容
    finished = pyqtSignal()
    error_occurred = pyqtSignal(str)
    file_progress = pyqtSignal(int, int)

    def __init__(self, api_url, api_key, model_name, input_files, output_dir, output_format, prompt_template, temperature):
        super().__init__()
        self.api_url = api_url
        self.api_key = api_key
        self.model_name = model_name
        self.input_files = input_files
        self.output_dir = output_dir
        self.output_format = output_format
        self.prompt_template = prompt_template
        self.temperature = temperature  # 用户自定义温度
        self._is_running = True
        self.current_file_index = 0
        self.current_item_index = 0
        self.total_items = 0
        self.stream_worker = None
        self.full_response = ""  # 用于累积完整响应

    def stop(self):
        self._is_running = False
        if self.stream_worker:
            self.stream_worker.stop()

    def calculate_progress(self):
        total_files = len(self.input_files)
        if total_files == 0:
            return 0
        file_progress = self.current_file_index / total_files
        if self.total_items > 0:
            item_progress = self.current_item_index / self.total_items
        else:
            item_progress = 0
        overall_progress = (file_progress + item_progress / total_files) * 100
        return int(overall_progress)

    def handle_new_token(self, token):
        """累积 token 并更新预览"""
        self.full_response += token
        self.preview_response.emit(token, False)

    def process_single_item(self, case_content):
        try:
            # 构建完整提示词
            prompt = self.prompt_template.format(case_content=case_content)
            
            # 准备请求数据（非流式模式仅用于预览显示）
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            max_tokens = calculate_auto_max_tokens(prompt)
            
            payload = {
                "model": self.model_name,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "max_tokens": max_tokens,
                "temperature": self.temperature,
                "top_p": 0.8
            }

            request_json = json.dumps(payload, indent=2, ensure_ascii=False)
            self.preview_request.emit(f"=== 请求内容 ===\n{request_json}\n")
            
            # 重置完整响应
            self.full_response = ""
            
            # 创建流式工作线程
            self.stream_worker = StreamWorker(
                self.api_url,
                self.api_key,
                self.model_name,
                prompt,
                self.temperature
            )
            
            # 将新 token 信号连接到 handle_new_token 方法
            self.stream_worker.new_token.connect(self.handle_new_token)
            self.stream_worker.finished.connect(lambda: None)
            self.stream_worker.error_occurred.connect(self.on_stream_error)
            
            self.stream_worker.start()
            
            # 等待流式线程处理完毕
            while self.stream_worker.isRunning():
                if not self._is_running:
                    self.stream_worker.stop()
                    return None
                QThread.msleep(100)
            
            # 最终标识完整响应结束
            self.preview_response.emit("\n=== 完整响应结束 ===\n", True)
            
            # 根据输出格式构造最终结果
            if self.output_format == "alpaca-format":
                output_data = {
                    "instruction": "分析案件逻辑、推导案例结果、寻找逻辑意义和案件现实意义",
                    "input": f"案件内容:\n{case_content}\n\n请按照以下要求分析:\n1. 分析案件逻辑\n2. 推导案例结果\n3. 寻找逻辑意义\n4. 分析案件现实意义",
                    "output": self.full_response
                }
            elif self.output_format == "openai-format":
                output_data = {
                    "messages": [
                        {"role": "system", "content": "你是一个法律分析专家，需要详细分析案件。"},
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": self.full_response}
                    ]
                }
            else:
                output_data = {
                    "prompt": prompt,
                    "response": self.full_response,
                    "source": case_content,
                    "metadata": {
                        "model": self.model_name,
                        "temperature": self.temperature,
                        "max_tokens": max_tokens
                    }
                }
            
            return json.dumps(output_data, ensure_ascii=False, indent=2)

        except Exception as e:
            raise Exception(f"处理过程中出错: {str(e)}")

    def on_stream_error(self, error_msg):
        self.error_occurred.emit(error_msg)

    def run(self):
        try:
            total_files = len(self.input_files)
            if total_files == 0:
                raise Exception("没有需要处理的文件")

            self.file_progress.emit(0, total_files)

            for file_idx, file_path in enumerate(self.input_files):
                if not self._is_running:
                    break

                self.current_file_index = file_idx
                self.log_message.emit(f"\n=== 正在处理文件 {file_idx+1}/{total_files}: {os.path.basename(file_path)} ===")
                
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                except IOError as e:
                    self.log_message.emit(f"文件读取失败: {str(e)}")
                    continue

                self.total_items = len(lines)
                self.current_item_index = 0
                output_lines = []

                for line_idx, line in enumerate(lines):
                    if not self._is_running:
                        break

                    self.current_item_index = line_idx
                    self.log_message.emit(f"处理项目 {line_idx+1}/{self.total_items}")

                    try:
                        data = json.loads(line.strip())
                        case_content = json.dumps(data, ensure_ascii=False, indent=2)
                        
                        result_line = self.process_single_item(case_content)
                        if result_line:
                            output_lines.append(result_line)
                        
                        progress = self.calculate_progress()
                        self.progress_updated.emit(progress)

                    except Exception as e:
                        self.log_message.emit(f"处理出错: {str(e)}")
                        continue

                if output_lines:
                    output_filename = os.path.join(
                        self.output_dir,
                        f"processed_{os.path.splitext(os.path.basename(file_path))[0]}.jsonl"
                    )
                    try:
                        with open(output_filename, 'w', encoding='utf-8') as f:
                            f.write('\n'.join(output_lines))
                        self.log_message.emit(f"√ 已保存结果到: {output_filename}")
                    except IOError as e:
                        self.log_message.emit(f"文件保存失败: {str(e)}")

                self.file_progress.emit(file_idx + 1, total_files)

            self.finished.emit()

        except Exception as e:
            self.error_occurred.emit(str(e))

class PreviewDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("完整内容预览")
        self.setGeometry(100, 100, 1200, 900)
        
        main_layout = QVBoxLayout()
        self.tab_widget = QTabWidget()
        
        # 请求内容标签页
        self.request_tab = QWidget()
        req_layout = QVBoxLayout()
        self.request_edit = QTextEdit()
        self.request_edit.setReadOnly(True)
        self.request_edit.setLineWrapMode(QTextEdit.NoWrap)
        self.request_edit.setFont(QFont("Consolas", 10))
        req_layout.addWidget(self.request_edit)
        self.request_tab.setLayout(req_layout)
        
        # 响应内容标签页
        self.response_tab = QWidget()
        resp_layout = QVBoxLayout()
        self.response_edit = QTextEdit()
        self.response_edit.setReadOnly(True)
        self.response_edit.setLineWrapMode(QTextEdit.NoWrap)
        self.response_edit.setFont(QFont("Consolas", 10))
        resp_layout.addWidget(self.response_edit)
        self.response_tab.setLayout(resp_layout)
        
        self.tab_widget.addTab(self.request_tab, "请求内容")
        self.tab_widget.addTab(self.response_tab, "响应内容")
        
        btn_layout = QHBoxLayout()
        self.clear_btn = QPushButton("清空所有")
        self.clear_btn.clicked.connect(self.clear_all)
        btn_layout.addWidget(self.clear_btn)
        
        self.save_request_btn = QPushButton("保存请求")
        self.save_request_btn.clicked.connect(lambda: self.save_content("request"))
        btn_layout.addWidget(self.save_request_btn)
        
        self.save_response_btn = QPushButton("保存响应")
        self.save_response_btn.clicked.connect(lambda: self.save_content("response"))
        btn_layout.addWidget(self.save_response_btn)
        
        main_layout.addWidget(self.tab_widget)
        main_layout.addLayout(btn_layout)
        self.setLayout(main_layout)
        
        self.scroll_timer = QTimer()
        self.scroll_timer.timeout.connect(self.auto_scroll)
        self.scroll_timer.start(100)
        
        self.typewriter_timer = QTimer()
        self.typewriter_timer.timeout.connect(self.update_typewriter_effect)
        self.typewriter_buffer = ""
        self.typewriter_active = False

    def auto_scroll(self):
        current_edit = self.request_edit if self.tab_widget.currentIndex() == 0 else self.response_edit
        cursor = current_edit.textCursor()
        cursor.movePosition(QTextCursor.End)
        current_edit.setTextCursor(cursor)

    def clear_all(self):
        self.request_edit.clear()
        self.response_edit.clear()

    def save_content(self, content_type):
        if content_type == "request":
            content = self.request_edit.toPlainText()
            default_name = "request.txt"
        else:
            content = self.response_edit.toPlainText()
            default_name = "response.txt"
        if not content.strip():
            QMessageBox.warning(self, "警告", "没有内容可保存")
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存内容", default_name, "文本文件 (*.txt);;所有文件 (*)"
        )
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                QMessageBox.information(self, "成功", "内容已保存")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"保存失败: {str(e)}")

    def append_response(self, text, is_final=False):
        cursor = self.response_edit.textCursor()
        cursor.movePosition(QTextCursor.End)
        if is_final:
            cursor.insertText(text)
        else:
            if not self.typewriter_active:
                self.typewriter_active = True
                self.typewriter_buffer = text
                self.typewriter_timer.start(50)
            else:
                self.typewriter_buffer += text

    def update_typewriter_effect(self):
        if self.typewriter_buffer:
            cursor = self.response_edit.textCursor()
            cursor.movePosition(QTextCursor.End)
            char = self.typewriter_buffer[0]
            cursor.insertText(char)
            self.typewriter_buffer = self.typewriter_buffer[1:]
            self.response_edit.ensureCursorVisible()
        else:
            self.typewriter_timer.stop()
            self.typewriter_active = False

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("大模型问答对生成工具 v3.0 (带打字机效果)")
        self.setGeometry(100, 100, 1400, 1000)
        
        self.worker = None
        self.worker_thread = None
        
        self.init_ui()
        self.setup_connections()
        self.load_config()
    
    def init_ui(self):
        self.create_menu_bar()
        main_widget = QWidget()
        main_layout = QVBoxLayout()
        
        self.setup_api_group(main_layout)
        self.setup_file_group(main_layout)
        self.setup_output_group(main_layout)
        self.setup_progress_group(main_layout)
        self.setup_log_group(main_layout)
        self.setup_button_group(main_layout)
        
        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)
        
        self.preview_dialog = PreviewDialog(self)
        self.statusBar().showMessage("就绪")
        self.setStyleSheet("""
            QMainWindow { background-color: #f5f5f5; }
            QTextEdit, QListWidget { border: 1px solid #ccc; border-radius: 3px; padding: 5px; background-color: white; }
            QPushButton { padding: 5px 10px; border: 1px solid #aaa; border-radius: 3px; background-color: #f0f0f0; min-width: 80px; }
            QPushButton:hover { background-color: #e0e0e0; }
            QPushButton:pressed { background-color: #d0d0d0; }
            QProgressBar { border: 1px solid #aaa; border-radius: 3px; text-align: center; }
        """)
    
    def create_menu_bar(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("文件")
        self.import_config_action = QAction("导入配置", self)
        self.export_config_action = QAction("导出配置", self)
        exit_action = QAction("退出", self)
        file_menu.addAction(self.import_config_action)
        file_menu.addAction(self.export_config_action)
        file_menu.addSeparator()
        file_menu.addAction(exit_action)
        exit_action.triggered.connect(self.close)
    
    def setup_api_group(self, layout):
        api_group = QWidget()
        api_layout = QHBoxLayout()
        
        api_layout.addWidget(QLabel("API URL:"))
        self.api_url_edit = QLineEdit("https://api.siliconflow.cn/v1/chat/completions")
        self.api_url_edit.setPlaceholderText("请输入API端点URL")
        api_layout.addWidget(self.api_url_edit, stretch=4)
        
        api_layout.addWidget(QLabel("API Key:"))
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setPlaceholderText("请输入API密钥")
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        api_layout.addWidget(self.api_key_edit, stretch=3)
        
        api_layout.addWidget(QLabel("模型名称:"))
        self.model_name_edit = QLineEdit("Qwen/QwQ-32B")
        self.model_name_edit.setPlaceholderText("请输入模型名称")
        api_layout.addWidget(self.model_name_edit, stretch=2)
        
        api_layout.addWidget(QLabel("Temperature:"))
        self.temperature_spin = QDoubleSpinBox()
        self.temperature_spin.setRange(0.0, 1.0)
        self.temperature_spin.setSingleStep(0.1)
        self.temperature_spin.setValue(0.7)
        api_layout.addWidget(self.temperature_spin, stretch=1)
        
        api_group.setLayout(api_layout)
        layout.addWidget(api_group)
    
    def setup_file_group(self, layout):
        file_group = QWidget()
        file_layout = QHBoxLayout()
        
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QListWidget.ExtendedSelection)
        file_layout.addWidget(self.file_list, stretch=3)
        
        file_btn_layout = QVBoxLayout()
        self.add_files_btn = QPushButton("添加文件")
        self.add_files_btn.setToolTip("添加一个或多个JSONL文件")
        file_btn_layout.addWidget(self.add_files_btn)
        
        self.add_dir_btn = QPushButton("添加目录")
        self.add_dir_btn.setToolTip("添加目录下的所有JSONL文件")
        file_btn_layout.addWidget(self.add_dir_btn)
        
        self.remove_selected_btn = QPushButton("移除选中")
        self.remove_selected_btn.setToolTip("移除选中的文件")
        file_btn_layout.addWidget(self.remove_selected_btn)
        
        self.clear_files_btn = QPushButton("清空列表")
        self.clear_files_btn.setToolTip("清空所有文件")
        file_btn_layout.addWidget(self.clear_files_btn)
        file_btn_layout.addStretch()
        file_layout.addLayout(file_btn_layout, stretch=1)
        file_group.setLayout(file_layout)
        layout.addWidget(file_group)
    
    def setup_output_group(self, layout):
        output_group = QWidget()
        output_layout = QHBoxLayout()
        
        output_layout.addWidget(QLabel("输出目录:"))
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setPlaceholderText("请选择输出目录")
        output_layout.addWidget(self.output_dir_edit, stretch=3)
        
        self.browse_dir_btn = QPushButton("浏览...")
        output_layout.addWidget(self.browse_dir_btn, stretch=1)
        
        output_layout.addWidget(QLabel("输出格式:"))
        self.format_combo = QComboBox()
        self.format_combo.addItems(["alpaca-format", "openai-format", "custom-format"])
        self.format_combo.setCurrentIndex(0)
        output_layout.addWidget(self.format_combo, stretch=2)
        
        output_group.setLayout(output_layout)
        layout.addWidget(output_group)
    
    def setup_progress_group(self, layout):
        progress_group = QWidget()
        progress_layout = QVBoxLayout()
        
        self.file_progress_label = QLabel("文件进度: 0/0")
        progress_layout.addWidget(self.file_progress_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(True)
        progress_layout.addWidget(self.progress_bar)
        
        self.current_file_label = QLabel("当前文件: 无")
        progress_layout.addWidget(self.current_file_label)
        
        progress_group.setLayout(progress_layout)
        layout.addWidget(progress_group)
    
    def setup_log_group(self, layout):
        log_group = QWidget()
        log_layout = QVBoxLayout()
        log_layout.addWidget(QLabel("处理日志:"))
        
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setFont(QFont("Consolas", 10))
        log_layout.addWidget(self.log_edit)
        log_group.setLayout(log_layout)
        layout.addWidget(log_group)
    
    def setup_button_group(self, layout):
        btn_group = QWidget()
        btn_layout = QHBoxLayout()
        
        self.start_btn = QPushButton("开始处理")
        self.start_btn.setStyleSheet("background-color: #4CAF50; color: white;")
        btn_layout.addWidget(self.start_btn)
        
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setStyleSheet("background-color: #f44336; color: white;")
        self.stop_btn.setEnabled(False)
        btn_layout.addWidget(self.stop_btn)
        
        self.preview_btn = QPushButton("预览完整内容")
        self.preview_btn.setEnabled(False)
        btn_layout.addWidget(self.preview_btn)
        
        self.clear_log_btn = QPushButton("清空日志")
        btn_layout.addWidget(self.clear_log_btn)
        
        btn_group.setLayout(btn_layout)
        layout.addWidget(btn_group)
    
    def setup_connections(self):
        self.add_files_btn.clicked.connect(self.add_files)
        self.add_dir_btn.clicked.connect(self.add_directory)
        self.remove_selected_btn.clicked.connect(self.remove_selected_files)
        self.clear_files_btn.clicked.connect(self.clear_files)
        self.browse_dir_btn.clicked.connect(self.browse_output_dir)
        self.start_btn.clicked.connect(self.start_processing)
        self.stop_btn.clicked.connect(self.stop_processing)
        self.preview_btn.clicked.connect(self.show_preview)
        self.clear_log_btn.clicked.connect(self.clear_log)
        self.import_config_action.triggered.connect(self.import_config)
        self.export_config_action.triggered.connect(self.export_config)
    
    def load_config(self):
        settings = QSettings(CONFIG_FILE, QSettings.IniFormat)
        api_url = settings.value("api/url", "https://api.siliconflow.cn/v1/chat/completions")
        model_name = settings.value("api/model", "Qwen/QwQ-32B")
        output_format = settings.value("output/format", "alpaca-format")
        temperature = float(settings.value("api/temperature", 0.7))
        
        self.api_url_edit.setText(api_url)
        self.model_name_edit.setText(model_name)
        self.temperature_spin.setValue(temperature)
        index = self.format_combo.findText(output_format)
        if index >= 0:
            self.format_combo.setCurrentIndex(index)
    
    def save_config(self):
        settings = QSettings(CONFIG_FILE, QSettings.IniFormat)
        settings.setValue("api/url", self.api_url_edit.text())
        settings.setValue("api/model", self.model_name_edit.text())
        settings.setValue("output/format", self.format_combo.currentText())
        settings.setValue("api/temperature", self.temperature_spin.value())
        settings.sync()
    
    def import_config(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择配置文件", "", "INI文件 (*.ini);;所有文件 (*)"
        )
        if file_path:
            settings = QSettings(file_path, QSettings.IniFormat)
            api_url = settings.value("api/url", "")
            api_key = settings.value("api/key", "")
            model_name = settings.value("api/model", "")
            output_format = settings.value("output/format", "")
            temperature = float(settings.value("api/temperature", 0.7))
            
            if api_url:
                self.api_url_edit.setText(api_url)
            if api_key:
                self.api_key_edit.setText(api_key)
            if model_name:
                self.model_name_edit.setText(model_name)
            if output_format:
                index = self.format_combo.findText(output_format)
                if index >= 0:
                    self.format_combo.setCurrentIndex(index)
            self.temperature_spin.setValue(temperature)
            self.statusBar().showMessage(f"已导入配置: {os.path.basename(file_path)}")
    
    def export_config(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存配置文件", "config.ini", "INI文件 (*.ini)"
        )
        if file_path:
            settings = QSettings(file_path, QSettings.IniFormat)
            settings.setValue("api/url", self.api_url_edit.text())
            settings.setValue("api/key", self.api_key_edit.text())
            settings.setValue("api/model", self.model_name_edit.text())
            settings.setValue("output/format", self.format_combo.currentText())
            settings.setValue("api/temperature", self.temperature_spin.value())
            settings.sync()
            self.statusBar().showMessage(f"配置已导出到: {file_path}")
    
    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择JSONL文件", "", "JSON Lines Files (*.jsonl *.json);;All Files (*)"
        )
        if files:
            self.file_list.addItems(files)
            self.statusBar().showMessage(f"已添加 {len(files)} 个文件")
            self.save_config()
    
    def add_directory(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择目录")
        if dir_path:
            jsonl_files = []
            for root, _, files in os.walk(dir_path):
                for file in files:
                    if file.lower().endswith(('.jsonl', '.json')):
                        jsonl_files.append(os.path.join(root, file))
            if jsonl_files:
                self.file_list.addItems(jsonl_files)
                self.statusBar().showMessage(f"从目录添加了 {len(jsonl_files)} 个文件")
                self.save_config()
            else:
                QMessageBox.information(self, "提示", "所选目录中没有找到JSONL文件")
    
    def remove_selected_files(self):
        selected_items = self.file_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "警告", "请先选择要移除的文件")
            return
        for item in selected_items:
            self.file_list.takeItem(self.file_list.row(item))
        self.statusBar().showMessage(f"已移除 {len(selected_items)} 个文件")
        self.save_config()
    
    def clear_files(self):
        if self.file_list.count() > 0:
            reply = QMessageBox.question(self, "确认", "确定要清空文件列表吗?", QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.file_list.clear()
                self.statusBar().showMessage("已清空文件列表")
                self.save_config()
        else:
            QMessageBox.information(self, "提示", "文件列表已经是空的")
    
    def browse_output_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if dir_path:
            self.output_dir_edit.setText(dir_path)
            self.statusBar().showMessage(f"输出目录设置为: {dir_path}")
            self.save_config()
    
    def clear_log(self):
        self.log_edit.clear()
        self.statusBar().showMessage("日志已清空")
    
    def validate_inputs(self):
        if self.file_list.count() == 0:
            QMessageBox.warning(self, "警告", "请先添加要处理的文件")
            return False
        if not self.output_dir_edit.text():
            QMessageBox.warning(self, "警告", "请选择输出目录")
            return False
        if not os.path.exists(self.output_dir_edit.text()):
            try:
                os.makedirs(self.output_dir_edit.text())
            except OSError as e:
                QMessageBox.warning(self, "错误", f"无法创建输出目录: {str(e)}")
                return False
        if not self.api_key_edit.text():
            QMessageBox.warning(self, "警告", "请输入API Key")
            return False
        if not self.api_url_edit.text():
            QMessageBox.warning(self, "警告", "请输入API URL")
            return False
        return True
    
    def start_processing(self):
        if not self.validate_inputs():
            return
        
        prompt_template = (
            "请分析以下案件内容，并按照以下四点要求进行详细分析：\n"
            "1. 分析案件逻辑 - 详细解释案件中的关键逻辑关系\n"
            "2. 推导案例结果 - 根据案件内容推导可能的结果\n"
            "3. 寻找逻辑意义 - 分析案件中的逻辑意义和价值\n"
            "4. 案件现实意义 - 探讨案件在现实中的意义和影响\n\n"
            "案件内容如下:\n{case_content}"
        )
        temperature = self.temperature_spin.value()
        self.worker = Worker(
            api_url=self.api_url_edit.text(),
            api_key=self.api_key_edit.text(),
            model_name=self.model_name_edit.text(),
            input_files=[self.file_list.item(i).text() for i in range(self.file_list.count())],
            output_dir=self.output_dir_edit.text(),
            output_format=self.format_combo.currentText(),
            prompt_template=prompt_template,
            temperature=temperature
        )
        
        # 使用 QThread 运行 Worker
        self.worker_thread = QThread()
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        
        self.worker.progress_updated.connect(self.update_progress)
        self.worker.log_message.connect(self.log_message)
        self.worker.preview_request.connect(self.preview_dialog.request_edit.append)
        self.worker.preview_response.connect(self.handle_preview_response)
        self.worker.finished.connect(self.processing_finished)
        self.worker.error_occurred.connect(self.handle_error)
        self.worker.file_progress.connect(self.update_file_progress)
        
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.log_message("=== 开始处理 ===")
        
        self.worker_thread.start()
        self.statusBar().showMessage("处理中...")
    
    def handle_preview_response(self, text, is_final):
        if is_final:
            self.preview_dialog.response_edit.append(text)
        else:
            self.preview_dialog.append_response(text)
    
    def stop_processing(self):
        if self.worker:
            self.worker.stop()
            self.log_message("正在停止处理...")
            self.statusBar().showMessage("正在停止...")
    
    def processing_finished(self):
        self.log_message("=== 处理完成 ===")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        # 清理线程
        if self.worker_thread:
            self.worker_thread.quit()
            self.worker_thread.wait()
        self.worker = None
        self.worker_thread = None
        self.statusBar().showMessage("处理完成")
        self.save_config()
    
    def handle_error(self, error_msg):
        self.log_message(f"错误: {error_msg}")
        self.processing_finished()
        QMessageBox.critical(self, "错误", f"处理过程中发生错误:\n{error_msg}")
    
    def update_progress(self, value):
        self.progress_bar.setValue(value)
    
    def update_file_progress(self, current, total):
        self.file_progress_label.setText(f"文件进度: {current}/{total}")
        if current > 0 and total > 0:
            self.current_file_label.setText(f"当前文件: {os.path.basename(self.file_list.item(current-1).text())}")
    
    def log_message(self, message):
        self.log_edit.append(message)
        cursor = self.log_edit.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.log_edit.setTextCursor(cursor)
    
    def show_preview(self):
        self.preview_dialog.show()
        self.preview_dialog.raise_()
        self.preview_dialog.activateWindow()

if __name__ == "__main__":
    app = QApplication([])
    app.setStyle("Fusion")
    if hasattr(QIcon, 'setThemeName'):
        QIcon.setThemeName('breeze')
    window = MainWindow()
    window.show()
    app.exec_()
