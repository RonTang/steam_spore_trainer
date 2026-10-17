import sys
import time
import queue
import threading

import tkinter as tk
from tkinter import ttk

import pymem
from pymem.process import module_from_name

from tkwebui import (enable_dpi_awareness, apply_theme, Tooltip,
                     PlaceholderEntry, Modal, WindowChrome, PALETTE)

enable_dpi_awareness()   # 必须在创建 Tk() 之前调用，高分屏不发虚

PROCESS_NAME = "SporeApp.exe"

# ---------------- 链配置 ----------------
# lockable: 是否提供锁定开关（默认True，无需锁定功能可设为False）
# hint:     可选，输入框占位符；缺省按 lockable 自动生成
CHAINS = {
    "生命": dict(module_offset=0x12DBA14,
                 offsets=(0x40, 0x12C, 0xF8, 0x8, 0x54),
                 is_float=True,  delta=0, lockable=True),
    "能量": dict(module_offset=0x12DBA14,
                 offsets=(0x40, 0x12C, 0xF8, 0x8, 0x54),
                 is_float=True,  delta=0x2B4, lockable=True),
    "金钱": dict(module_offset=0x127EB44,
                 offsets=(0x88, 0x28, 0x1EC, 0xD0),
                 is_float=False, delta=0, lockable=True),
    # 修改：航程（不需要锁定）
    "航程": dict(module_offset=0x12DC0FC,
                 offsets=(0x5C, 0x30, 0x34, 0x3C, 0x1CC),
                 is_float=True,  delta=0, lockable=False,
                 hint="仅初始引擎生效，回车修改"),
    # 修改：可视范围（更新了最新的指针链，并加入自定义提示语）
    "可视范围": dict(module_offset=0x12DC0FC,
                 offsets=(0x40, 0x34, 0x3C, 0x1B0),
                 is_float=True,  delta=0, lockable=False,
                 hint="仅初始引擎生效，回车修改"),
}

# ---------------- 关于窗口文案 ----------------
ABOUT_STORY = [
    "这是一个爸爸写给儿子的修改器。",
    "儿子今年五岁半，迷上了《孢子》。他捏的小生物爬上了陆地、建起了部落、"
    "创立了城市，终于盼到了太空阶段——然后他的飞船第一次被打爆。"
    "他愣了几秒钟，哇一声哭了出来。",
    "那天晚上他睡着以后，我开始自学 Cheat Engine。一个三十多岁、白天还要上班的人，"
    "对着满屏的十六进制，从“指针链”三个字完全看不懂，到一点点摸到门路。"
    "游戏每更新一次，好不容易找到的指针就整条作废，只能从头再来。"
    "中间崩溃过的次数，比他被击落的飞船还多。",
    "但只要想到他发现新恒星时发亮的眼睛，就觉得这些深夜都值了。",
    "现在，他的飞船不会再被打爆了。他可以慢慢飞，飞到任何他想去的地方，"
    "看每一颗他想看的星星。这大概就是爸爸能给的、最小的超能力。",
    "如果你也是一位陪孩子玩游戏的家长，希望这个修改器也能帮到你们。"
    "愿每个孩子的宇宙，都又大又安全。",
]

ABOUT_FEATURES = [
    "生命 / 能量 —— 拨动开关即锁定，太空阶段再不翻车；"
    "锁定值留空时，锁定的是拨开关那一刻的数值",
    "金钱 —— 输入数值回车，立即到账",
    "航程 / 可视范围 —— 针对初始引擎，回车修改一次（升级引擎后失效）",
]

ABOUT_FOOTER = "本修改器仅在 Steam 正版《孢子》上验证通过，游戏版本更新后功能可能失效。"
ABOUT_SIGN = "谨献给我的小宇航员 · v1.0"

import tkinter.font as _tkfont

def _fill_wrap(text, max_px, font):
    """按字体真实像素宽度逐字贪心填充，在算好的位置手动插换行，
    实现 CJK 式断行（英文单词该断就断），杜绝行尾大空白。
    Tk 的 wraplength 只认空格/CJK 断点，会整词下沉留白，所以不依赖它。"""
    lines, cur, cur_w = [], "", 0
    for ch in text:
        w = font.measure(ch)
        if cur and cur_w + w > max_px:
            lines.append(cur)
            cur, cur_w = ch, w
        else:
            cur += ch
            cur_w += w
    lines.append(cur)
    return "\n".join(lines)

def resolve(pm, cfg):
    base = module_from_name(pm.process_handle, PROCESS_NAME).lpBaseOfDll
    addr = base + cfg["module_offset"]
    for off in cfg["offsets"]:
        addr = pm.read_int(addr) + off
    return addr + cfg["delta"]

def read_val(pm, cfg, addr):
    return pm.read_float(addr) if cfg["is_float"] else pm.read_int(addr)

def write_val(pm, cfg, addr, v):
    if cfg["is_float"]:
        pm.write_float(addr, float(v))
    else:
        pm.write_int(addr, int(v))

# ---------------- 界面 ----------------
class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Steam版Spore太空阶段修改器")
        apply_theme(self.root)                  # 创建控件之前调用
        self.root.geometry("640x420")
        self.root.resizable(False, False)       # 需配合打过补丁的 WindowChrome

        if sys.platform == "win32":
            # 自定义标题栏：浅灰工具条与白色内容区形成分界，
            # 右侧窗口按钮由 WindowChrome 提供
            toolbar = ttk.Frame(self.root, style="Sidebar.TFrame")
            toolbar.pack(fill="x")
            # 用显式颜色的 tk.Label，不依赖 Sidebar.Strong.TLabel 复合样式
            tk.Label(toolbar, text="Steam版Spore太空阶段修改器",
                     bg=PALETTE["sidebar"], fg=PALETTE["fg"],
                     font=("Microsoft YaHei UI", 10, "bold"),
                     padx=14, pady=10).pack(side="left")
            ttk.Button(toolbar, text="关于", style="Quiet.TButton", takefocus=False,
                       command=self.show_about).pack(side="left", padx=(4, 0))
            # toolbar 整体可拖动（其上的按钮/标签会被自动排除在拖动区外）
            chrome = WindowChrome(self.root, toolbar, drag_widgets=(toolbar,),
                                  on_close=self.on_close)
            # 按钮区背景与工具条同色（库默认 TFrame 是白色补丁）
            chrome.controls.configure(style="Sidebar.TFrame")
            # 尺寸锁定：藏掉最大化按钮、禁用双击最大化
            # （配合打过补丁的库，系统级最大化也已被 ptMaxSize 锁死）
            chrome.buttons["maximize"].pack_forget()
            chrome.toggle_maximize = lambda: None
            # attach 是 after_idle 异步执行的，相关控件可能尚未就绪，轮询等待
            def _polish_chrome():
                if not chrome.resize_handles:
                    self.root.after(50, _polish_chrome)
                    return
                # 固定尺寸窗口不需要缩放手柄：手柄是白色长条，
                # 会盖住标题栏左右两缘（"缺背景色"的真凶）
                for h in chrome.resize_handles.values():
                    h.destroy()
                chrome.resize_handles.clear()
                chrome.resize_margin = 0   # 连窗口边缘的 resize 热区一起去掉
                # 库的 refresh_cache 会把 resize_margin 重置回 5，
                # 包一层保持我们的 0（否则锁尺寸窗口边缘仍出缩放光标）
                _orig_refresh = chrome.refresh_cache
                def _refresh_keep_margin():
                    _orig_refresh()
                    chrome.resize_margin = 0
                chrome.refresh_cache = _refresh_keep_margin
                # 窗口按钮统一换成"标题栏同色底"的平边按钮
                # （Quiet.TButton 是白色药丸，在灰工具条上很突兀）
                from tkwebui.core import icon as _icon

                def _flat_button(key, name, hover_bg, hover_color=None):
                    chrome.buttons[key].pack_forget()
                    b = tk.Button(chrome.controls, bd=0, highlightthickness=0,
                                  relief="flat", cursor="hand2",
                                  bg=PALETTE["sidebar"], activebackground=hover_bg)
                    state = {"img": None}
                    def img(color, hov=False):
                        im = _icon(self.root, name, chrome.icon_size, color=color,
                                   background=hover_bg if hov else PALETTE["sidebar"])
                        state["img"] = im   # 防止 PhotoImage 被回收
                        return im
                    def refresh(hov=False):
                        if key == "pin" and chrome.pinned:
                            color = PALETTE["blue"]
                        elif hov and hover_color:
                            color = hover_color
                        else:
                            color = PALETTE["muted"]
                        b.configure(image=img(color, hov))
                    def cmd():
                        getattr(chrome, {"pin": "toggle_pin",
                                         "minimize": "minimize",
                                         "close": "close"}[key])()
                        # close 会 destroy 整个窗口（控件甚至整个 Tk 解释器
                        # 都不复存在），刷新静默跳过即可
                        try:
                            refresh()
                        except tk.TclError:
                            pass
                    b.configure(command=cmd)
                    b.bind("<Enter>", lambda e: refresh(True))
                    b.bind("<Leave>", lambda e: refresh(False))
                    refresh()
                    return b

                pin_btn = _flat_button("pin", "pin", PALETTE["hover"])
                min_btn = _flat_button("minimize", "minimize", PALETTE["hover"])
                # 关闭按钮：Windows 系统红悬停（#E81123 底 + 白叉）
                close_btn = _flat_button("close", "close", "#E81123",
                                         hover_color="#FFFFFF")
                # 按原顺序排回：置顶 → 最小化 → 关闭（最大化已隐藏）
                # 四个原始按钮此时已全部 pack_forget，packing 列表为空，
                # 直接链式 after 排列：置顶 → 最小化 → 关闭
                pin_btn.pack(side="left", padx=5)
                min_btn.pack(side="left", padx=5, after=pin_btn)
                close_btn.pack(side="left", padx=(5, 12), after=min_btn)
                Tooltip(pin_btn, lambda: "取消置顶" if chrome.pinned else "窗口置顶")
                Tooltip(min_btn, "最小化")
                Tooltip(close_btn, "关闭")
            self.root.after(50, _polish_chrome)
        else:
            # 非 Windows 保留原生标题栏，关于放右上角按钮
            ttk.Button(self.root, text="关于", width=6,
                       command=self.show_about).place(x=560, y=16)

        body = ttk.Frame(self.root, padding=20)
        body.pack(fill="both", expand=True)

        # 表头
        ttk.Label(body, text="变量").grid(row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Label(body, text="锁定值").grid(row=0, column=1, sticky="w", padx=(16, 0), pady=(0, 8))
        ttk.Label(body, text="锁定").grid(row=0, column=2, sticky="w", padx=(16, 0), pady=(0, 8))
        ttk.Label(body, text="当前值").grid(row=0, column=3, sticky="w", padx=(16, 0), pady=(0, 8))

        self.rows = {}           # 名字 -> dict(edt=, sw=, cur=)
        self.locked = {}         # worker 独占（handle_cmd 写，worker 循环读）
        self.lock_target = {}    # worker 独占; None 表示"下次读到值时捕获当前"
        self.write_once = {}     # 名字 -> 回车请求的一次性写入值（worker 独占）
        self.cmd_q = queue.Queue()   # UI -> worker 的唯一通道，单向

        for i, name in enumerate(CHAINS):
            r = i + 1
            ttk.Label(body, text=name).grid(row=r, column=0, sticky="w", pady=6)

            hint = CHAINS[name].get("hint") or (
                "留空 = 锁定当前" if CHAINS[name].get("lockable", True)
                else "回车修改一次")
            edt = PlaceholderEntry(body, width=30, placeholder=hint)
            edt.grid(row=r, column=1, sticky="w", padx=(16, 0), pady=6)
            edt.bind("<Return>", lambda e, n=name: self.on_enter(n))

            # 判断是否提供锁定开关：圆形滑动开关样式，不建 BooleanVar，
            # 状态直接读控件的 selected 状态，避免退出时 Variable.__del__ 报错
            if CHAINS[name].get("lockable", True):
                sw = ttk.Checkbutton(body, text="", style="Switch.TCheckbutton",
                                     command=lambda n=name: self.on_toggle(n))
                sw.grid(row=r, column=2, sticky="w", padx=(16, 0), pady=6)
            else:
                sw = None

            cur = ttk.Label(body, text="--")
            cur.grid(row=r, column=3, sticky="w", padx=(16, 0), pady=6)
            self.rows[name] = dict(edt=edt, sw=sw, cur=cur)
            self.locked[name] = False
            self.lock_target[name] = None

        self.last_cur = {n: None for n in CHAINS}
        self.status = ttk.Label(self.root, text="等待游戏运行...", padding=(20, 4))
        self.status.pack(anchor="w")

        # 退出处理
        self.running = True
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        threading.Thread(target=self.worker, daemon=True).start()
        self.root.mainloop()

    # ---------- 关于窗口 ----------
    def show_about(self):
        modal = Modal(self.root, "关于这个修改器", width=520, height=400)
        body = modal.body
        scale = modal.scale

        # 换行宽度按主窗口宽度预算（单位：像素）。
        # Modal 宽度被钳制在 主窗口宽-60 像素内，再减去弹窗内边距(2×22×scale)、
        # 滚动条占位和安全余量；任何 DPI 下每行文字都完整，不会被画布右缘切掉。
        wrap = self.root.winfo_width() - 60 - round(44 * scale) - round(26 * scale)
        wrap = max(round(220 * scale), wrap)
        # 与 TLabel 同款的主题字体，用于逐字量宽；留 6px 余量抵消测宽误差
        font = _tkfont.nametofont("TkDefaultFont")
        max_px = wrap - round(6 * scale)

        for para in ABOUT_STORY:
            ttk.Label(body, text=_fill_wrap(para, max_px, font),
                      anchor="w", justify="left").pack(fill="x", pady=(0, round(8 * scale)))
        ttk.Separator(body).pack(fill="x", pady=round(10 * scale))

        ttk.Label(body, text="主要功能",
                  style="Strong.TLabel").pack(anchor="w", pady=(0, round(4 * scale)))
        for feat in ABOUT_FEATURES:
            ttk.Label(body, text=_fill_wrap("· " + feat, max_px, font),
                      anchor="w", justify="left").pack(fill="x", pady=(0, round(3 * scale)))
        ttk.Separator(body).pack(fill="x", pady=round(10 * scale))

        ttk.Label(body, text=_fill_wrap(ABOUT_FOOTER, max_px, font),
                  anchor="w", justify="left", style="Muted.TLabel").pack(fill="x", pady=(0, round(3 * scale)))
        ttk.Label(body, text=ABOUT_SIGN,
                  style="Muted.TLabel").pack(anchor="e")

        # 底部按钮：只用自带的"关闭"，藏掉"取消"
        modal.primary.configure(text="关闭")
        modal.secondary.pack_forget()

        # 尺寸自适应。注意：Modal.target_width / target_height 的单位是
        # 像素（构造函数里 round(width*scale) 即逻辑转像素），这里直接
        # 给像素值，不要再除 scale
        modal.update_idletasks()
        modal.target_width = wrap + round(60 * scale)
        modal.target_height = body.winfo_reqheight() + round(160 * scale)
        modal.position()

    # ---------- UI 线程回调（唯一允许碰 widget 的地方） ----------
    def on_close(self):
        self.running = False
        self.root.destroy()

    def on_toggle(self, name):
        sw = self.rows[name]["sw"]
        if sw is None:
            return
        on = bool(sw.instate(["selected"]))
        # 拨动开关的那一刻快照目标值；打开且输入框为空 → target=None，
        # 由 worker 在下次读到值时捕获当前值
        target = None
        if on:
            target = self.parse_entry(name)
            raw = self.rows[name]["edt"].get().strip()
            if raw and target is None:
                self.set_status(f"{name}: 输入无效, 将锁定当前值")
        self.cmd_q.put(("set_lock", name, on, target))
        # 注意：self.locked 不在此写入，worker 独占

    def on_enter(self, name):
        """输入框回车:
        - 未锁定: 立即写入一次（不锁定）
        - 锁定中: 更新锁定目标为新值，继续锁定
        """
        v = self.parse_entry(name)
        if v is None:
            self.set_status(f"{name}: 输入为空或无效, 未写入")
            return
        sw = self.rows[name]["sw"]
        if sw is not None and sw.instate(["selected"]):
            self.cmd_q.put(("set_lock", name, True, v))
            self.set_status(f"{name}: 锁定目标已更新为 {v}")
        else:
            self.cmd_q.put(("write_once", name, v))

    def parse_entry(self, name):
        """只能在主线程调用"""
        text = self.rows[name]["edt"].get().strip()
        if not text:
            return None
        try:
            v = float(text)
        except ValueError:
            return None
        return int(v) if not CHAINS[name]["is_float"] else v

    # ---------- 跨线程刷新 ----------
    def set_status(self, s):
        if not self.running:
            return
        try:
            self.root.after(0, lambda: self.status.configure(text=s))
        except (RuntimeError, tk.TclError):   # destroy 窗口期两者都可能抛
            pass

    def set_cur(self, name, s):
        if not self.running or self.last_cur.get(name) == s:
            return
        self.last_cur[name] = s
        try:
            self.root.after(0, lambda: self.rows[name]["cur"].configure(text=s))
        except (RuntimeError, tk.TclError):
            pass

    # ---------- 工作线程（不碰任何 Tk 对象） ----------
    def handle_cmd(self, cmd):
        op = cmd[0]
        if op == "write_once":
            _, name, v = cmd
            self.write_once[name] = v
        elif op == "set_lock":
            _, name, on, target = cmd
            self.locked[name] = on
            self.lock_target[name] = target

    def worker(self):
        pm = None
        while self.running:
            # 1) 消费 UI 指令（纯 Python 数据结构，安全）
            try:
                while self.running:
                    self.handle_cmd(self.cmd_q.get_nowait())
            except queue.Empty:
                pass
            if not self.running:
                break

            # 2) 附加 / 重连
            if pm is None:
                try:
                    pm = pymem.Pymem(PROCESS_NAME)
                    self.set_status("已附加到游戏")
                except Exception:
                    self.set_status("等待游戏运行...")
                    time.sleep(2)
                    continue

            # 3) 逐链读写
            for name, cfg in CHAINS.items():
                if not self.running:
                    break
                try:
                    addr = resolve(pm, cfg)
                    cur = read_val(pm, cfg, addr)
                    self.set_cur(name,
                                 f"{cur:.2f}" if cfg["is_float"] else str(cur))

                    if name in self.write_once:     # 回车触发的即时写入
                        # 注意：若 resolve/read 抛异常，write_once 保留到下轮
                        # 重连后重试——这是请求重试语义，不要改成 finally pop
                        v = self.write_once.pop(name)
                        write_val(pm, cfg, addr, v)
                        self.set_status(f"{name} 已写入地址{addr:x},数值{v} (未锁定)")

                    if not self.locked[name]:      # 没开锁定 → 只读
                        continue

                    target = self.lock_target[name]
                    if target is None:
                        # 开关打开时输入框为空 → 锁定当前值（捕获后写回，固定）
                        self.lock_target[name] = cur
                        target = cur

                    write_val(pm, cfg, addr, target)
                except Exception:
                    pm = None        # 链失效/游戏重启 → 下轮重连
                    self.set_status("连接中断, 重连中...")
                    break

            time.sleep(0.1)

if __name__ == "__main__":
    App()
