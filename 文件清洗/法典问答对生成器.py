import sys
import requests
import json
import time
from PyQt5.QtCore import pyqtSignal, QThread
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QPushButton, QLabel,
    QLineEdit, QFileDialog, QVBoxLayout, QHBoxLayout,
    QWidget, QProgressBar, QTextEdit
)

# ----------------------------
# API 配置，请替换 <token> 为你的实际 token
API_URL = "https://api.siliconflow.cn/v1/chat/completions"
API_TOKEN = "sk-xdnehhaewnabtreismizgxdryxovjcxqwqldtampbiowdhwg"  # 请替换为你的实际 token

HEADERS = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json"
}

def call_api(prompt):
    """
    调用硅基流动 API，根据 prompt 得到大模型返回的文本。
    这里假设返回格式与 OpenAI Chat API 类似：从 data["choices"][0]["message"]["content"] 中提取答案。
    """
    payload = {
        "model": "Qwen/Qwen2.5-32B-Instruct",
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "stream": False,
        "max_tokens": 512,
        "stop": ["null"],
        "temperature": 0.7,
        "top_p": 0.7,
        "top_k": 50,
        "frequency_penalty": 0.5,
        "n": 1,
        "response_format": {"type": "text"},
        "tools": [
            {
                "type": "function",
                "function": {
                    "description": "<string>",
                    "name": "<string>",
                    "parameters": {},
                    "strict": False
                }
            }
        ]
    }
    try:
        response = requests.post(API_URL, json=payload, headers=HEADERS)
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return content.strip()
    except Exception as e:
        print("调用 API 失败：", e)
        return None

# ----------------------------
# Worker 线程：用于后台处理转换任务
class Worker(QThread):
    # 定义信号，用于更新进度和日志
    progress_changed = pyqtSignal(int)
    log_message = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, input_file, output_file):
        super(Worker, self).__init__()
        self.input_file = input_file
        self.output_file = output_file

    def run(self):
        # 读取输入文件（按行切分，忽略空行）
        try:
            with open(self.input_file, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
        except Exception as e:
            self.log_message.emit(f"读取文件失败: {e}")
            self.finished.emit()
            return

        total = len(lines)
        self.log_message.emit(f"共找到 {total} 条法条。")
        try:
            out_f = open(self.output_file, "w", encoding="utf-8")
        except Exception as e:
            self.log_message.emit(f"无法打开输出文件: {e}")
            self.finished.emit()
            return

        # 遍历每条法条
        for idx, law_text in enumerate(lines):
            self.log_message.emit(f"正在处理第 {idx+1}/{total} 条法条...")
            # 1. 根据法条生成查询问题
            question_prompt = (
                "请根据下面的民法典法条生成一个用于查询该法条内容及解释的问题，"
                "例如“第xxx条的内容是什么？怎么理解？”；请只返回问题，不要其他内容。\n\n"
                f"{law_text}"
            )
            question = call_api(question_prompt)
            if question is None:
                question = "【生成问题失败】"
                self.log_message.emit("生成问题失败。")

            # 2. 根据法条生成解释
            explanation_prompt = (
                "请对下面的民法典法条进行详细解释，要求解释内容必须包含该法条的原文及对其的说明，"
                "请只返回解释内容，不要其他文字。\n\n"
                f"{law_text}"
            )
            explanation = call_api(explanation_prompt)
            if explanation is None:
                explanation = "【生成解释失败】"
                self.log_message.emit("生成解释失败。")

            # 将原文和解释组合为答案
            answer = f"{law_text}\n\n解释：{explanation}"

            # 构造符合 LoRA 训练要求的问答数据，要求：
            # - messages 数组中第一个消息为 user（问题），第二个为 assistant（答案）
            conversation = {
                "messages": [
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": answer}
                ]
            }

            # 写入一行 JSON 对象（jsonl 格式）
            out_f.write(json.dumps(conversation, ensure_ascii=False) + "\n")

            # 更新进度
            progress = int((idx + 1) / total * 100)
            self.progress_changed.emit(progress)

            # 根据需要添加延时，防止 API 速率限制
            time.sleep(1)

        out_f.close()
        self.log_message.emit("全部法条处理完成！")
        self.finished.emit()

# ----------------------------
# 主窗口：基于 PyQt5 构建 GUI
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LoRA 训练 jsonl 生成器")
        self.resize(600, 400)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # 输入文件选择控件
        input_layout = QHBoxLayout()
        self.input_line_edit = QLineEdit()
        self.input_button = QPushButton("选择输入文件")
        self.input_button.clicked.connect(self.select_input_file)
        input_layout.addWidget(QLabel("输入文件:"))
        input_layout.addWidget(self.input_line_edit)
        input_layout.addWidget(self.input_button)
        layout.addLayout(input_layout)

        # 输出文件选择控件
        output_layout = QHBoxLayout()
        self.output_line_edit = QLineEdit()
        self.output_button = QPushButton("选择输出文件")
        self.output_button.clicked.connect(self.select_output_file)
        output_layout.addWidget(QLabel("输出文件:"))
        output_layout.addWidget(self.output_line_edit)
        output_layout.addWidget(self.output_button)
        layout.addLayout(output_layout)

        # 开始按钮
        self.start_button = QPushButton("开始生成")
        self.start_button.clicked.connect(self.start_processing)
        layout.addWidget(self.start_button)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        # 日志显示区域
        self.log_text_edit = QTextEdit()
        self.log_text_edit.setReadOnly(True)
        layout.addWidget(self.log_text_edit)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

    def select_input_file(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "选择输入txt文件", "", "Text Files (*.txt);;All Files (*)"
        )
        if filename:
            self.input_line_edit.setText(filename)

    def select_output_file(self):
        filename, _ = QFileDialog.getSaveFileName(
            self, "选择输出jsonl文件", "", "JSONL Files (*.jsonl);;All Files (*)"
        )
        if filename:
            self.output_line_edit.setText(filename)

    def start_processing(self):
        input_file = self.input_line_edit.text().strip()
        output_file = self.output_line_edit.text().strip()
        if not input_file or not output_file:
            self.log("请先选择输入和输出文件。")
            return

        self.start_button.setEnabled(False)
        self.log("任务开始……")
        self.worker = Worker(input_file, output_file)
        self.worker.progress_changed.connect(self.update_progress)
        self.worker.log_message.connect(self.log)
        self.worker.finished.connect(self.processing_finished)
        self.worker.start()

    def update_progress(self, value):
        self.progress_bar.setValue(value)

    def log(self, message):
        self.log_text_edit.append(message)

    def processing_finished(self):
        self.start_button.setEnabled(True)
        self.log("任务已完成。")

# ----------------------------
def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
