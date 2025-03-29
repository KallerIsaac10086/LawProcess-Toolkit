import tkinter as tk
from tkinter import filedialog, messagebox
import threading
import re
import pandas as pd
import os

def convert_file(file_path, output_path, status_callback):
    """
    读取 jsonl 或 txt 文件，提取步数、损失率和 lr 数据，然后保存为 Excel 文件。
    lr 的值将放大 10000 倍。
    status_callback 为更新状态信息的回调函数。
    """
    try:
        data = []
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                # 提取括号内的步数，例如：(8400/44160) 提取 8400
                step_match = re.search(r'\((\d+)/', line)
                # 提取 loss 后面的数字，例如 loss:2.485
                loss_match = re.search(r'loss:(\d+\.?\d*)', line)
                # 提取 lr 后面的数字，例如 lr:0.000506674126
                lr_match = re.search(r'lr:(\d+\.?\d*)', line)
                if step_match and loss_match and lr_match:
                    step = step_match.group(1)
                    loss = loss_match.group(1)
                    lr_value = float(lr_match.group(1)) * 10000  # 放大 10000 倍
                    data.append([step, loss, lr_value])
        
        # 利用 pandas 创建 DataFrame，列依次为步数、损失率和 lr（放大 10000 倍）
        df = pd.DataFrame(data, columns=["步数", "损失率", "lr*10000"])
        # 保存为 Excel 文件，不保存行索引
        df.to_excel(output_path, index=False)
        status_callback("转换完成，Excel 文件已保存为:\n" + output_path)
    except Exception as e:
        status_callback("发生错误: " + str(e))

def start_conversion():
    """
    开始转换操作：获取文件路径，选择保存路径，并在新线程中执行转换操作。
    """
    file_path = file_entry.get()
    if not file_path or not os.path.exists(file_path):
        messagebox.showerror("错误", "请选择有效的文件 (.jsonl 或 .txt)")
        return
    
    # 选择保存 Excel 文件的路径
    output_path = filedialog.asksaveasfilename(defaultextension=".xlsx", 
                                               filetypes=[("Excel 文件", "*.xlsx")],
                                               title="保存 Excel 文件")
    if not output_path:
        return
    
    status_label.config(text="转换中...")
    # 在新线程中执行转换，防止界面卡顿
    convert_thread = threading.Thread(
        target=convert_file, 
        args=(file_path, output_path, lambda msg: root.after(0, update_status, msg))
    )
    convert_thread.start()

def update_status(message):
    """
    更新状态标签显示的信息。
    """
    status_label.config(text=message)

def browse_file():
    """
    弹出文件选择对话框，选择 jsonl 或 txt 文件。
    """
    file_path = filedialog.askopenfilename(filetypes=[("JSONL和TXT文件", "*.jsonl *.txt"), ("所有文件", "*.*")],
                                           title="选择 jsonl 或 txt 文件")
    if file_path:
        file_entry.delete(0, tk.END)
        file_entry.insert(0, file_path)

# 创建主窗口
root = tk.Tk()
root.title("文件转换工具 (.jsonl/.txt 转 Excel)")

# 文件选择区域
file_frame = tk.Frame(root)
file_frame.pack(padx=10, pady=10)

file_entry = tk.Entry(file_frame, width=50)
file_entry.pack(side=tk.LEFT, padx=(0, 5))

browse_button = tk.Button(file_frame, text="选择文件", command=browse_file)
browse_button.pack(side=tk.LEFT)

# 转换按钮
convert_button = tk.Button(root, text="开始转换", command=start_conversion)
convert_button.pack(pady=(0, 10))

# 状态显示标签
status_label = tk.Label(root, text="等待转换")
status_label.pack(pady=(0, 10))

root.mainloop()
