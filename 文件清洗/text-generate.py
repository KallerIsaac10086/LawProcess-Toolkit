import os
import json
import threading
import requests
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QProgressBar, QFileDialog,
    QComboBox, QTextEdit, QListWidget, QMessageBox, QDialog, QScrollArea,
    QSplitter, QTabWidget, QMenuBar, QMenu, QAction, QToolBar
)
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QTimer, QSettings, QThread, pyqtSlot
from PyQt5.QtGui import QTextCursor, QFont, QIcon, QTextCharFormat, QColor

CONFIG_FILE = "config.ini"

class StreamWorker(QThread):
    """用于流式接收API响应的线程"""
    new_token = pyqtSignal(str)
    finished = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, api_url, api_key, model_name, prompt):
        super().__init__()
        self.api_url = api_url
        self.api_key = api_key
        self.model_name = model_name
        self.prompt = prompt
        self._is_running = True

    def run(self):
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream"
            }
            
            payload = {
                "model": self.model_name,
                "messages": [{"role": "user", "content": self.prompt}],
                "stream": True,
                "max_tokens": 4096,
                "temperature": 0.7,
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
    preview_response = pyqtSignal(str, bool)  # 添加是否完成的标志
    finished = pyqtSignal()
    error_occurred = pyqtSignal(str)
    file_progress = pyqtSignal(int, int)

    def __init__(self, api_url, api_key, model_name, input_files, output_dir, output_format, prompt_template):
        super().__init__()
        self.api_url = api_url
        self.api_key = api_key
        self.model_name = model_name
        self.input_files = input_files
        self.output_dir = output_dir
        self.output_format = output_format
        self.prompt_template = prompt_template
        self._is_running = True
        self.current_file_index = 0
        self.current_item_index = 0
        self.total_items = 0
        self.stream_worker = None

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

    def process_single_item(self, case_content):
        try:
            # 构建完整提示词
            prompt = self.prompt_template.format(case_content=case_content)
            
            # 准备请求数据
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": self.model_name,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "max_tokens": 4096,
                "temperature": 0.7,
                "top_p": 0.8
            }

            # 发送完整请求内容到预览
            request_json = json.dumps(payload, indent=2, ensure_ascii=False)
            self.preview_request.emit(f"=== 请求内容 ===\n{request_json}\n")
            
            # 创建流式工作线程
            self.stream_worker = StreamWorker(
                self.api_url,
                self.api_key,
                self.model_name,
                prompt
            )
            
            # 连接流式工作线程的信号
            self.stream_worker.new_token.connect(lambda token: self.preview_response.emit(token, False))
            self.stream_worker.finished.connect(self.on_stream_finished)
            self.stream_worker.error_occurred.connect(self.on_stream_error)
            
            # 启动流式工作线程
            self.stream_worker.start()
            
            # 等待流式工作线程完成
            while self.stream_worker.isRunning():
                if not self._is_running:
                    self.stream_worker.stop()
                    return None
                QApplication.processEvents()
                self.stream_worker.wait(100)
            
            # 获取完整响应
            self.preview_response.emit("\n=== 完整响应结束 ===\n", True)
            
            # 根据格式构建输出
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
                        "temperature": 0.7,
                        "max_tokens": 4096
                    }
                }
            
            return json.dumps(output_data, ensure_ascii=False, indent=2)

        except Exception as e:
            raise Exception(f"处理过程中出错: {str(e)}")

    def on_stream_finished(self):
        """流式处理完成"""
        self.stream_worker = None

    def on_stream_error(self, error_msg):
        """流式处理出错"""
        self.error_occurred.emit(error_msg)

    def run(self):
        """主处理逻辑"""
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
                        
                        # 重置完整响应
                        self.full_response = ""
                        
                        # 处理单个项目
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
        
        # 主布局
        main_layout = QVBoxLayout()
        
        # 创建标签页
        self.tab_widget = QTabWidget()
        
        # 请求标签页
        self.request_tab = QWidget()
        request_layout = QVBoxLayout()
        self.request_edit = QTextEdit()
        self.request_edit.setReadOnly(True)
        self.request_edit.setLineWrapMode(QTextEdit.NoWrap)
        self.request_edit.setFont(QFont("Consolas", 10))
        request_layout.addWidget(self.request_edit)
        self.request_tab.setLayout(request_layout)
        
        # 响应标签页
        self.response_tab = QWidget()
        response_layout = QVBoxLayout()
        self.response_edit = QTextEdit()
        self.response_edit.setReadOnly(True)
        self.response_edit.setLineWrapMode(QTextEdit.NoWrap)
        self.response_edit.setFont(QFont("Consolas", 10))
        
        # 设置打字机效果样式
        self.typewriter_format = QTextCharFormat()
        self.typewriter_format.setForeground(QColor("#4CAF50"))  # 绿色文字
        
        self.normal_format = QTextCharFormat()
        self.normal_format.setForeground(QColor("#000000"))  # 黑色文字
        
        response_layout.addWidget(self.response_edit)
        self.response_tab.setLayout(response_layout)
        
        # 添加标签页
        self.tab_widget.addTab(self.request_tab, "请求内容")
        self.tab_widget.addTab(self.response_tab, "响应内容")
        
        # 底部按钮
        button_layout = QHBoxLayout()
        self.clear_btn = QPushButton("清空所有")
        self.clear_btn.clicked.connect(self.clear_all)
        button_layout.addWidget(self.clear_btn)
        
        self.save_request_btn = QPushButton("保存请求")
        self.save_request_btn.clicked.connect(lambda: self.save_content("request"))
        button_layout.addWidget(self.save_request_btn)
        
        self.save_response_btn = QPushButton("保存响应")
        self.save_response_btn.clicked.connect(lambda: self.save_content("response"))
        button_layout.addWidget(self.save_response_btn)
        
        # 组装主布局
        main_layout.addWidget(self.tab_widget)
        main_layout.addLayout(button_layout)
        
        self.setLayout(main_layout)
        
        # 自动滚动定时器
        self.scroll_timer = QTimer()
        self.scroll_timer.timeout.connect(self.auto_scroll)
        self.scroll_timer.start(100)
        
        # 打字机效果定时器
        self.typewriter_timer = QTimer()
        self.typewriter_timer.timeout.connect(self.update_typewriter_effect)
        self.typewriter_buffer = ""
        self.typewriter_active = False
    
    def auto_scroll(self):
        """自动滚动到底部"""
        current_edit = self.request_edit if self.tab_widget.currentIndex() == 0 else self.response_edit
        cursor = current_edit.textCursor()
        cursor.movePosition(QTextCursor.End)
        current_edit.setTextCursor(cursor)
    
    def clear_all(self):
        """清空所有内容"""
        self.request_edit.clear()
        self.response_edit.clear()
    
    def save_content(self, content_type):
        """保存内容到文件"""
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
        """追加响应文本，支持打字机效果"""
        cursor = self.response_edit.textCursor()
        cursor.movePosition(QTextCursor.End)
        
        if is_final:
            # 最终内容，直接添加
            cursor.insertText(text, self.normal_format)
        else:
            # 流式内容，使用打字机效果
            if not self.typewriter_active:
                self.typewriter_active = True
                self.typewriter_buffer = text
                self.typewriter_timer.start(50)  # 每50毫秒添加一个字符
            else:
                self.typewriter_buffer += text
    
    def update_typewriter_effect(self):
        """更新打字机效果"""
        if len(self.typewriter_buffer) > 0:
            cursor = self.response_edit.textCursor()
            cursor.movePosition(QTextCursor.End)
            
            # 添加一个字符
            char = self.typewriter_buffer[0]
            cursor.insertText(char, self.typewriter_format)
            
            # 从缓冲区移除已添加的字符
            self.typewriter_buffer = self.typewriter_buffer[1:]
            
            # 自动滚动
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
        
        # 初始化UI
        self.init_ui()
        self.setup_connections()
        
        # 加载配置
        self.load_config()
    
    def init_ui(self):
        """初始化用户界面"""
        # 创建菜单栏
        self.create_menu_bar()
        
        # 主部件和布局
        main_widget = QWidget()
        main_layout = QVBoxLayout()
        
        # 添加各个组件
        self.setup_api_group(main_layout)
        self.setup_file_group(main_layout)
        self.setup_output_group(main_layout)
        self.setup_progress_group(main_layout)
        self.setup_log_group(main_layout)
        self.setup_button_group(main_layout)
        
        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)
        
        # 预览对话框
        self.preview_dialog = PreviewDialog(self)
        
        # 状态栏
        self.statusBar().showMessage("就绪")
        
        # 设置样式
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f5f5f5;
            }
            QGroupBox {
                border: 1px solid #ddd;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 15px;
            }
            QTextEdit, QListWidget {
                border: 1px solid #ccc;
                border-radius: 3px;
                padding: 5px;
                background-color: white;
            }
            QPushButton {
                padding: 5px 10px;
                border: 1px solid #aaa;
                border-radius: 3px;
                background-color: #f0f0f0;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #e0e0e0;
            }
            QPushButton:pressed {
                background-color: #d0d0d0;
            }
            QProgressBar {
                border: 1px solid #aaa;
                border-radius: 3px;
                text-align: center;
            }
        """)
    
    def create_menu_bar(self):
        """创建菜单栏"""
        menubar = self.menuBar()
        
        # 文件菜单
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
        """设置API配置组"""
        api_group = QWidget()
        api_layout = QHBoxLayout()
        
        # API URL
        api_layout.addWidget(QLabel("API URL:"))
        self.api_url_edit = QLineEdit("https://api.siliconflow.cn/v1/chat/completions")
        self.api_url_edit.setPlaceholderText("请输入API端点URL")
        api_layout.addWidget(self.api_url_edit, stretch=4)
        
        # API Key
        api_layout.addWidget(QLabel("API Key:"))
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setPlaceholderText("请输入API密钥")
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        api_layout.addWidget(self.api_key_edit, stretch=3)
        
        # 模型名称
        api_layout.addWidget(QLabel("模型名称:"))
        self.model_name_edit = QLineEdit("Qwen/QwQ-32B")
        self.model_name_edit.setPlaceholderText("请输入模型名称")
        api_layout.addWidget(self.model_name_edit, stretch=2)
        
        api_group.setLayout(api_layout)
        layout.addWidget(api_group)
    
    def setup_file_group(self, layout):
        """设置文件选择组"""
        file_group = QWidget()
        file_layout = QHBoxLayout()
        
        # 文件列表
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QListWidget.ExtendedSelection)
        file_layout.addWidget(self.file_list, stretch=3)
        
        # 文件操作按钮
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
        """设置输出配置组"""
        output_group = QWidget()
        output_layout = QHBoxLayout()
        
        # 输出目录
        output_layout.addWidget(QLabel("输出目录:"))
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setPlaceholderText("请选择输出目录")
        output_layout.addWidget(self.output_dir_edit, stretch=3)
        
        self.browse_dir_btn = QPushButton("浏览...")
        output_layout.addWidget(self.browse_dir_btn, stretch=1)
        
        # 输出格式
        output_layout.addWidget(QLabel("输出格式:"))
        self.format_combo = QComboBox()
        self.format_combo.addItems(["alpaca-format", "openai-format", "custom-format"])
        self.format_combo.setCurrentIndex(0)
        output_layout.addWidget(self.format_combo, stretch=2)
        
        output_group.setLayout(output_layout)
        layout.addWidget(output_group)
    
    def setup_progress_group(self, layout):
        """设置进度显示组"""
        progress_group = QWidget()
        progress_layout = QVBoxLayout()
        
        # 文件进度
        self.file_progress_label = QLabel("文件进度: 0/0")
        progress_layout.addWidget(self.file_progress_label)
        
        # 总体进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(True)
        progress_layout.addWidget(self.progress_bar)
        
        # 当前文件进度
        self.current_file_label = QLabel("当前文件: 无")
        progress_layout.addWidget(self.current_file_label)
        
        progress_group.setLayout(progress_layout)
        layout.addWidget(progress_group)
    
    def setup_log_group(self, layout):
        """设置日志显示组"""
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
        """设置操作按钮组"""
        btn_group = QWidget()
        btn_layout = QHBoxLayout()
        
        # 开始按钮
        self.start_btn = QPushButton("开始处理")
        self.start_btn.setStyleSheet("background-color: #4CAF50; color: white;")
        btn_layout.addWidget(self.start_btn)
        
        # 停止按钮
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setStyleSheet("background-color: #f44336; color: white;")
        self.stop_btn.setEnabled(False)
        btn_layout.addWidget(self.stop_btn)
        
        # 预览按钮
        self.preview_btn = QPushButton("预览完整内容")
        self.preview_btn.setEnabled(False)
        btn_layout.addWidget(self.preview_btn)
        
        # 清空日志按钮
        self.clear_log_btn = QPushButton("清空日志")
        btn_layout.addWidget(self.clear_log_btn)
        
        btn_group.setLayout(btn_layout)
        layout.addWidget(btn_group)
    
    def setup_connections(self):
        """设置信号和槽连接"""
        # 文件操作
        self.add_files_btn.clicked.connect(self.add_files)
        self.add_dir_btn.clicked.connect(self.add_directory)
        self.remove_selected_btn.clicked.connect(self.remove_selected_files)
        self.clear_files_btn.clicked.connect(self.clear_files)
        
        # 目录操作
        self.browse_dir_btn.clicked.connect(self.browse_output_dir)
        
        # 处理操作
        self.start_btn.clicked.connect(self.start_processing)
        self.stop_btn.clicked.connect(self.stop_processing)
        self.preview_btn.clicked.connect(self.show_preview)
        self.clear_log_btn.clicked.connect(self.clear_log)
        
        # 菜单操作
        self.import_config_action.triggered.connect(self.import_config)
        self.export_config_action.triggered.connect(self.export_config)
    
    def load_config(self):
        """加载配置文件"""
        settings = QSettings(CONFIG_FILE, QSettings.IniFormat)
        
        # 读取配置值
        api_url = settings.value("api/url", "https://api.siliconflow.cn/v1/chat/completions")
        model_name = settings.value("api/model", "Qwen/QwQ-32B")
        output_format = settings.value("output/format", "alpaca-format")
        
        # 设置UI控件
        self.api_url_edit.setText(api_url)
        self.model_name_edit.setText(model_name)
        
        # 设置下拉框
        index = self.format_combo.findText(output_format)
        if index >= 0:
            self.format_combo.setCurrentIndex(index)
    
    def save_config(self):
        """保存配置文件"""
        settings = QSettings(CONFIG_FILE, QSettings.IniFormat)
        
        # 保存配置值
        settings.setValue("api/url", self.api_url_edit.text())
        settings.setValue("api/model", self.model_name_edit.text())
        settings.setValue("output/format", self.format_combo.currentText())
        
        # 不保存API Key，确保安全
        settings.sync()
    
    def import_config(self):
        """导入配置文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择配置文件", "", "INI文件 (*.ini);;所有文件 (*)"
        )
        
        if file_path:
            settings = QSettings(file_path, QSettings.IniFormat)
            
            # 读取配置值
            api_url = settings.value("api/url", "")
            api_key = settings.value("api/key", "")
            model_name = settings.value("api/model", "")
            output_format = settings.value("output/format", "")
            
            # 设置UI控件
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
            
            self.statusBar().showMessage(f"已导入配置: {os.path.basename(file_path)}")
    
    def export_config(self):
        """导出配置文件"""
        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存配置文件", "config.ini", "INI文件 (*.ini)"
        )
        
        if file_path:
            settings = QSettings(file_path, QSettings.IniFormat)
            
            # 保存配置值
            settings.setValue("api/url", self.api_url_edit.text())
            settings.setValue("api/key", self.api_key_edit.text())
            settings.setValue("api/model", self.model_name_edit.text())
            settings.setValue("output/format", self.format_combo.currentText())
            
            settings.sync()
            self.statusBar().showMessage(f"配置已导出到: {file_path}")
    
    def add_files(self):
        """添加文件到列表"""
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择JSONL文件", "", 
            "JSON Lines Files (*.jsonl *.json);;All Files (*)"
        )
        if files:
            self.file_list.addItems(files)
            self.statusBar().showMessage(f"已添加 {len(files)} 个文件")
            self.save_config()  # 自动保存配置
    
    def add_directory(self):
        """添加目录下的所有JSONL文件"""
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
                self.save_config()  # 自动保存配置
            else:
                QMessageBox.information(self, "提示", "所选目录中没有找到JSONL文件")
    
    def remove_selected_files(self):
        """移除选中的文件"""
        selected_items = self.file_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "警告", "请先选择要移除的文件")
            return
        
        for item in selected_items:
            self.file_list.takeItem(self.file_list.row(item))
        
        self.statusBar().showMessage(f"已移除 {len(selected_items)} 个文件")
        self.save_config()  # 自动保存配置
    
    def clear_files(self):
        """清空文件列表"""
        if self.file_list.count() > 0:
            reply = QMessageBox.question(
                self, "确认", 
                "确定要清空文件列表吗?", 
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self.file_list.clear()
                self.statusBar().showMessage("已清空文件列表")
                self.save_config()  # 自动保存配置
        else:
            QMessageBox.information(self, "提示", "文件列表已经是空的")
    
    def browse_output_dir(self):
        """选择输出目录"""
        dir_path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if dir_path:
            self.output_dir_edit.setText(dir_path)
            self.statusBar().showMessage(f"输出目录设置为: {dir_path}")
            self.save_config()  # 自动保存配置
    
    def clear_log(self):
        """清空日志"""
        self.log_edit.clear()
        self.statusBar().showMessage("日志已清空")
    
    def validate_inputs(self):
        """验证输入参数"""
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
        """开始处理文件"""
        if not self.validate_inputs():
            return
            
        # 提示词模板
        prompt_template = (
            "请分析以下案件内容，并按照以下四点要求进行详细分析：\n"
            "1. 分析案件逻辑 - 详细解释案件中的关键逻辑关系\n"
            "2. 推导案例结果 - 根据案件内容推导可能的结果\n"
            "3. 寻找逻辑意义 - 分析案件中的逻辑意义和价值\n"
            "4. 案件现实意义 - 探讨案件在现实中的意义和影响\n\n"
            "案件内容如下:\n{case_content}"
        )
        
        # 创建worker
        self.worker = Worker(
            api_url=self.api_url_edit.text(),
            api_key=self.api_key_edit.text(),
            model_name=self.model_name_edit.text(),
            input_files=[self.file_list.item(i).text() for i in range(self.file_list.count())],
            output_dir=self.output_dir_edit.text(),
            output_format=self.format_combo.currentText(),
            prompt_template=prompt_template
        )
        
        # 创建线程
        self.worker_thread = threading.Thread(target=self.worker.run)
        self.worker_thread.daemon = True
        
        # 连接信号
        self.worker.progress_updated.connect(self.update_progress)
        self.worker.log_message.connect(self.log_message)
        self.worker.preview_request.connect(self.preview_dialog.request_edit.append)
        self.worker.preview_response.connect(self.handle_preview_response)
        self.worker.finished.connect(self.processing_finished)
        self.worker.error_occurred.connect(self.handle_error)
        self.worker.file_progress.connect(self.update_file_progress)
        
        # 更新UI状态
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.log_message("=== 开始处理 ===")
        
        # 启动线程
        self.worker_thread.start()
        self.statusBar().showMessage("处理中...")
    
    def handle_preview_response(self, text, is_final):
        """处理预览响应"""
        if is_final:
            self.preview_dialog.response_edit.append(text)
        else:
            self.preview_dialog.append_response(text)
    
    def stop_processing(self):
        """停止处理"""
        if self.worker:
            self.worker.stop()
            self.log_message("正在停止处理...")
            self.statusBar().showMessage("正在停止...")
    
    def processing_finished(self):
        """处理完成"""
        self.log_message("=== 处理完成 ===")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.worker_thread = None
        self.worker = None
        self.statusBar().showMessage("处理完成")
        self.save_config()  # 自动保存配置
    
    def handle_error(self, error_msg):
        """处理错误"""
        self.log_message(f"错误: {error_msg}")
        self.processing_finished()
        QMessageBox.critical(self, "错误", f"处理过程中发生错误:\n{error_msg}")
    
    def update_progress(self, value):
        """更新进度条"""
        self.progress_bar.setValue(value)
    
    def update_file_progress(self, current, total):
        """更新文件进度"""
        self.file_progress_label.setText(f"文件进度: {current}/{total}")
        if current > 0 and total > 0:
            self.current_file_label.setText(f"当前文件: {os.path.basename(self.file_list.item(current-1).text())}")
    
    def log_message(self, message):
        """记录日志消息"""
        self.log_edit.append(message)
        cursor = self.log_edit.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.log_edit.setTextCursor(cursor)
    
    def show_preview(self):
        """显示预览对话框"""
        self.preview_dialog.show()
        self.preview_dialog.raise_()
        self.preview_dialog.activateWindow()

if __name__ == "__main__":
    app = QApplication([])
    app.setStyle("Fusion")
    
    # 设置应用程序图标
    if hasattr(QIcon, 'setThemeName'):
        QIcon.setThemeName('breeze')
    
    window = MainWindow()
    window.show()
    app.exec_()
