import tkinter as tk

left_count = 0
right_count = 0

def on_left_click():
    global left_count
    left_count += 1
    lbl_left_count.config(text=f"{left_count}回")
    print(f"Left button clicked! 合計: {left_count}回")

def on_right_click():
    global right_count
    right_count += 1
    lbl_right_count.config(text=f"{right_count}回")
    print(f"Right button clicked! 合計: {right_count}回")

root = tk.Tk()
root.title("テストボタン")
root.geometry("500x400")
root.resizable(False, False)

main_frame = tk.Frame(root, pady=20)
main_frame.pack()

# 左列 (3行)
left_col = tk.Frame(main_frame)
left_col.pack(side=tk.LEFT, padx=30)

for i in range(3):
    tk.Button(left_col, text="Left Button", width=15, height=2,
              bg="#4CAF50", fg="white", font=("Arial", 13),
              command=on_left_click).pack(pady=5)

# 右列 (3行)
right_col = tk.Frame(main_frame)
right_col.pack(side=tk.LEFT, padx=30)

for i in range(3):
    tk.Button(right_col, text="Right Button", width=15, height=2,
              bg="#2196F3", fg="white", font=("Arial", 13),
              command=on_right_click).pack(pady=5)

# カウント表示
count_frame = tk.Frame(root, pady=15)
count_frame.pack()

tk.Label(count_frame, text="Left: ", font=("Arial", 12)).pack(side=tk.LEFT)
lbl_left_count = tk.Label(count_frame, text="0回", font=("Arial", 12, "bold"), fg="#4CAF50")
lbl_left_count.pack(side=tk.LEFT, padx=(0, 30))

tk.Label(count_frame, text="Right: ", font=("Arial", 12)).pack(side=tk.LEFT)
lbl_right_count = tk.Label(count_frame, text="0回", font=("Arial", 12, "bold"), fg="#2196F3")
lbl_right_count.pack(side=tk.LEFT)

root.mainloop()
