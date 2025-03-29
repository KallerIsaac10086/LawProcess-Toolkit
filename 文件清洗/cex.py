import tkinter as tk
from tkinter import filedialog, messagebox
import json
import re
from datetime import datetime

ISAAC_NAME = "Isaac"  # 这里根据你聊天记录中的“Isaac”名字来定

def parse_file_to_entries(file_path):
    """
    解析单个文件，逐行读出:
      - 日期时间 (datetime对象)
      - 说话人 (string)
      - 发言内容 (多行合并为一个字符串)

    返回一个列表，元素形如:
      [
        (datetime_obj, speaker, "聊天内容..."),
        ...
      ]

    注意：此时不做“同一个人连续行合并”，也不做排序。
    """
    # 用于匹配形如 "2022-11-16 14:20:06 Isaac" 的行
    # group(1) 是日期时间字符串, group(2) 是人名
    pattern = re.compile(r'^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+(\S+)')

    entries = []
    current_timestamp = None
    current_speaker = None
    current_text_lines = []

    def commit_current_block():
        """ 将当前块加入 entries 列表，并重置。 """
        if current_timestamp and current_speaker is not None and current_text_lines:
            merged_text = "\n".join(current_text_lines)
            entries.append((current_timestamp, current_speaker, merged_text))

    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            raw = line.rstrip('\n').strip()
            if not raw:
                continue

            match = pattern.match(raw)
            if match:
                # 如果匹配到 "日期时间+人名" 形式，则把上一块提交
                commit_current_block()

                dt_str = match.group(1)  # "2022-11-16 14:20:06"
                speaker = match.group(2)
                # 解析 datetime
                dt_obj = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")

                # 开始一个新块
                current_timestamp = dt_obj
                current_speaker = speaker
                current_text_lines = []
            else:
                # 普通聊天内容，追加到 current_text_lines
                current_text_lines.append(raw)

    # 文件结束后，如果还有未提交的块，也要提交
    commit_current_block()

    return entries

def parse_multiple_files_with_time_sort(file_paths):
    """
    解析多个文件，汇总后根据时间排序，返回大列表:
      [
        (datetime_obj, speaker, "发言内容..."),
        ...
      ]
    这里不进行同说话人的合并，先仅按时间顺序排好。
    """
    all_entries = []
    for path in file_paths:
        single_entries = parse_file_to_entries(path)
        all_entries.extend(single_entries)

    # 按时间顺序排序
    all_entries.sort(key=lambda x: x[0])  # x[0] 即 datetime_obj
    return all_entries

def merge_consecutive_same_speaker(sorted_entries):
    """
    对按时间排序好的列表进行二次处理：
    如果有相邻的 (speaker) 相同，则把发言内容合并。
    返回形如:
      [
        ("speakerA", "合并后的内容..."),
        ("speakerB", "合并后的内容..."),
        ...
      ]
    不再需要时间，因为合并后才用于对话 pairing。
    """
    if not sorted_entries:
        return []

    merged = []
    current_speaker = sorted_entries[0][1]  # speaker
    current_text = sorted_entries[0][2]     # content

    for i in range(1, len(sorted_entries)):
        # (dt, speaker, text)
        speaker = sorted_entries[i][1]
        text = sorted_entries[i][2]
        if speaker == current_speaker:
            # 同一人，合并
            current_text += "\n" + text
        else:
            # 切换说话人，先把上一块加入
            merged.append((current_speaker, current_text))
            current_speaker = speaker
            current_text = text
    # 最后一块加入
    merged.append((current_speaker, current_text))

    return merged

def create_rounds_nonIsaac_to_Isaac(merged_list):
    """
    只保留 (非 Isaac) -> (Isaac) 这样的相邻对话。
    instruction = 非 Isaac
    output = Isaac
    """
    rounds = []
    i = 0
    while i < len(merged_list) - 1:
        speaker1, text1 = merged_list[i]
        speaker2, text2 = merged_list[i + 1]
        if speaker1 != ISAAC_NAME and speaker2 == ISAAC_NAME:
            rounds.append({
                "instruction": text1,
                "output": text2
            })
            i += 2
        else:
            i += 1
    return rounds

class MultiFileTimeSortGUI:
    def __init__(self, master):
        self.master = master
        self.master.title("多文件合并+时间排序+Isaac在output")

        self.file_paths = []
        self.json_text = ""

        frame_top = tk.Frame(master)
        frame_top.pack(padx=10, pady=10, fill="x")

        btn_select = tk.Button(frame_top, text="选择多个文件", command=self.choose_files)
        btn_select.pack(side="left", padx=5)

        btn_generate = tk.Button(frame_top, text="生成 JSON", command=self.generate_json)
        btn_generate.pack(side="left", padx=5)

        btn_save = tk.Button(frame_top, text="另存 JSON", command=self.save_json)
        btn_save.pack(side="left", padx=5)

        self.text_area = tk.Text(master, width=100, height=25)
        self.text_area.pack(padx=10, pady=5)

    def choose_files(self):
        paths = filedialog.askopenfilenames(
            title="选择多个聊天记录文件",
            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")]
        )
        if paths:
            self.file_paths = list(paths)
            self.text_area.delete("1.0", tk.END)
            self.text_area.insert(tk.END, "已选文件:\n\n")
            for p in self.file_paths:
                self.text_area.insert(tk.END, p + "\n")

    def generate_json(self):
        if not self.file_paths:
            messagebox.showwarning("警告", "请先选择至少一个文件！")
            return

        try:
            # 1. 多文件解析后按时间排序
            sorted_entries = parse_multiple_files_with_time_sort(self.file_paths)
            # 2. 将时间顺序的记录中，相邻同一人发言合并
            merged_by_speaker = merge_consecutive_same_speaker(sorted_entries)
            # 3. 生成只包含“(非Isaac) -> Isaac” 的对话
            rounds = create_rounds_nonIsaac_to_Isaac(merged_by_speaker)
            # 4. 转成 JSON
            self.json_text = json.dumps(rounds, ensure_ascii=False, indent=2)

            self.text_area.delete("1.0", tk.END)
            self.text_area.insert(tk.END, self.json_text)
            messagebox.showinfo("提示", "JSON 生成完毕！")

        except Exception as e:
            messagebox.showerror("错误", f"生成 JSON 失败：\n{e}")

    def save_json(self):
        if not self.json_text:
            messagebox.showwarning("警告", "请先生成 JSON！")
            return

        save_path = filedialog.asksaveasfilename(
            title="另存为",
            defaultextension=".json",
            filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")]
        )
        if save_path:
            try:
                with open(save_path, 'w', encoding='utf-8') as f:
                    f.write(self.json_text)
                messagebox.showinfo("提示", "JSON 保存成功！")
            except Exception as e:
                messagebox.showerror("错误", f"保存失败：\n{e}")


if __name__ == "__main__":
    root = tk.Tk()
    app = MultiFileTimeSortGUI(root)
    root.mainloop()
