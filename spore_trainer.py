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
        # 内饰底色换成浅蓝白（比 #EDF4FF 更亮白一档，#F4F8FF 几乎白但带一丝蓝）。
        # 必须在 apply_theme 之前改：tkwebui 的样式表是按 PALETTE 现值烘焙的，
        # 正文区/输入框/关于弹窗的底色全部随之改变
        from tkwebui import core as _tkcore
        _tkcore.PALETTE["bg"] = "#FBFBFF"
        apply_theme(self.root)                  # 创建控件之前调用
        self.root.geometry("640x420")
        self.root.resizable(False, False)       # 需配合打过补丁的 WindowChrome

        if sys.platform == "win32":
            # 渐变标题栏：Canvas 逐行插值画 蓝→白，底部自然过渡到白色正文区。
            # 按钮背景 = 其所覆盖区域的渐变切片图（PhotoImage 逐像素合成），
            # 真按钮也能"透明悬浮"在渐变上。渐变公式抽成 _bar_hex 供两处共用。
            BAR_H = 68
            TOP = (0x1E, 0x5B, 0xD6)          # 渐变顶色（深蓝）
            END = (0xCB, 0xDD, 0xF7)          # 渐变底色：留些蓝，不到纯白
            toolbar = tk.Canvas(self.root, height=BAR_H, highlightthickness=0,
                                bd=0, bg="#1E5BD6")
            toolbar.pack(fill="x", side="top")

            def _bar_hex(y, h):
                t = min(1.0, max(0.0, y / max(1, h - 1)))
                r = round(TOP[0] + (END[0] - TOP[0]) * t)
                g = round(TOP[1] + (END[1] - TOP[1]) * t)
                b = round(TOP[2] + (END[2] - TOP[2]) * t)
                return f"#{r:02x}{g:02x}{b:02x}"

            def _paint_bar(event=None):
                toolbar.delete("bg")
                w = max(1, toolbar.winfo_width())
                h = max(1, toolbar.winfo_height())
                for i in range(h):
                    toolbar.create_line(0, i, w, i, fill=_bar_hex(i, h), tags="bg")
                toolbar.tag_lower("bg")

            _paint_bar()
            toolbar.bind("<Configure>", lambda e: _paint_bar(), add="+")

            # 白色标题，垂直居中偏左
            toolbar.create_text(
                18, BAR_H // 2, text="Steam版Spore太空阶段修改器", anchor="w",
                fill="white", font=("Microsoft YaHei UI", 11, "bold"))

            def _chrome_ready():
                # ready-to-show：库补丁版由 on_ready 钩子触发（attach 完成
                # 后再晚一帧）；未补丁库由 after 轮询触发——此守卫两者通用
                if not chrome.resize_handles:
                    self.root.after(50, _chrome_ready)
                    return
                # attach 已完成、正文已在屏上（alpha=0 时）绘制完毕：
                # 1) 完成按钮自绘；2) update_idletasks 把隐藏状态下所有
                #    待完成的绘制强制收尾；3) 快速淡入——首帧的任何合成
                #    毛边都藏进渐进显现里
                _polish_chrome()
                self.root.update_idletasks()
                def _fade(step=0, steps=14):
                    try:
                        self.root.attributes("-alpha", (step + 1) / steps)
                    except tk.TclError:
                        return                        # 窗口已关闭
                    if step + 1 < steps:
                        try:
                            self.root.after(16, _fade, step + 1, steps)
                        except tk.TclError:
                            pass
                _fade()

            try:
                # 打过补丁的库支持 on_ready：attach 完成时精确触发，无需轮询
                chrome = WindowChrome(self.root, toolbar, drag_widgets=(toolbar,),
                                      on_close=self.on_close, on_ready=_chrome_ready)
            except TypeError:
                # 未补丁库：退化为轮询
                chrome = WindowChrome(self.root, toolbar, drag_widgets=(toolbar,),
                                      on_close=self.on_close)
                self.root.after(50, _chrome_ready)
            # 库的 controls 容器（含四枚 ttk 按钮）先藏，_polish 里换自绘、再销毁
            chrome.controls.pack_forget()
            # chrome 的 attach 必须等窗口映射后才执行，期间是原生标题栏状态。
            # 关键：窗口"可见"Windows 才会绘制它——屏外停放会导致正文控件
            # 从未被画过，首次显示一片黑矩形。所以窗口就放在最终位置（居中），
            # 用 alpha=0 隐藏：映射/绘制/attach 都正常进行，用户却看不见；
            # ready 回调里先完成按钮自绘，再把 alpha 归 1，首帧即是成品
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            self.root.geometry(f"640x420+{(sw - 640) // 2}+{(sh - 420) // 2}")
            self.root.attributes("-alpha", 0.0)
            # 尺寸锁定：禁用双击最大化
            # （配合打过补丁的库，系统级最大化也已被 ptMaxSize 锁死）
            chrome.toggle_maximize = lambda: None
            def _polish_chrome():
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
                # 库的 controls 容器是不透明灰框，会挡住渐变——整体弃用
                chrome.controls.destroy()
                chrome.paint_control = lambda *a, **k: None   # 按钮已全部自绘

                from tkwebui.core import icon as _icon

                # 统一按钮盒（含 "?"），嵌入画布时强制同尺寸
                _sc = self.root.winfo_fpixels("1i") / 96
                BOX = round(34 * _sc)
                BTN_Y = max(0, (toolbar.winfo_height() - BOX) // 2)

                def _box_image(color_fn):
                    """BOX×BOX 底图，color_fn(dy) 给每行颜色。
                    传 _grad_slice → 与标题栏天衣无缝；传常量 → 悬停底色。"""
                    img = tk.PhotoImage(width=BOX, height=BOX)
                    img.put(" ".join("{" + " ".join([color_fn(dy)] * BOX) + "}"
                                     for dy in range(BOX)))
                    return img

                _bar_h = max(1, toolbar.winfo_height())

                def _grad_slice(dy):
                    return _bar_hex(BTN_Y + dy, _bar_h)

                def _shade(hexcolor, f):
                    """颜色向顶蓝调 f（0~1）"""
                    rgb = (int(hexcolor[1:3], 16), int(hexcolor[3:5], 16),
                           int(hexcolor[5:7], 16))
                    return "#%02x%02x%02x" % tuple(round(c + (t - c) * f)
                                                  for c, t in zip(rgb, TOP))

                def _press_base():
                    """按压底图：切片向顶蓝调深 35%，但边缘 3px 内渐隐回原色——
                    按压感集中在按钮内部，与周围渐变无缝"""
                    img = tk.PhotoImage(width=BOX, height=BOX)
                    rows = []
                    for dy in range(BOX):
                        base = _grad_slice(dy)
                        toks = []
                        for dx in range(BOX):
                            d = min(dx, BOX - 1 - dx, dy, BOX - 1 - dy)
                            toks.append(_shade(base, 0.35 * min(1.0, d / 3)))
                        rows.append("{" + " ".join(toks) + "}")
                    img.put(" ".join(rows))
                    return img

                def _press_base_of(kind):
                    """各按钮的按压底图：关闭按钮用红色系（悬停红的浅版），
                    其余用蓝渐变的边缘渐隐调深"""
                    if kind == "close":
                        return _box_image(lambda dy: "#F06470")
                    return _press_base()

                _cache = {}

                def _glyph_box(key, color, base_img, off_y=0):
                    """库图标逐像素合成到底图上。背景键 #FF00FF。
                    对每个像素算投影系数 s（在 键色→笔画色 连线上的位置），
                    s 即笔画覆盖率：s≈1 纯笔画、s≈0 纯背景、中间为抗锯齿——
                    按 s 与底图对应行颜色混合，任意笔画色/底色都无杂边。"""
                    g = _icon(self.root, key, chrome.icon_size,
                              color=color, background="#FF00FF")
                    kx, ky, kz = 255, 0, 255
                    gr = int(color[1:3], 16); gg = int(color[3:5], 16); gb = int(color[5:7], 16)
                    dx, dy, dz = gr - kx, gg - ky, gb - kz
                    den = dx*dx + dy*dy + dz*dz
                    ox = (BOX - g.width()) // 2
                    oy = (BOX - g.height()) // 2 + off_y
                    bg_rows = [base_img.get(0, dy) for dy in range(BOX)]
                    for x in range(g.width()):
                        for y in range(g.height()):
                            c = g.get(x, y)
                            s = ((c[0]-kx)*dx + (c[1]-ky)*dy + (c[2]-kz)*dz) / den
                            if s <= 0.03:
                                continue
                            br, bg_, bb = bg_rows[oy + y]
                            if s >= 0.97:
                                put = color
                            else:
                                put = "#%02x%02x%02x" % (round(br + (gr - br) * s),
                                                         round(bg_ + (gg - bg_) * s),
                                                         round(bb + (gb - bb) * s))
                            base_img.put("{" + put + "}", to=(ox + x, oy + y))
                    return base_img

                # —— 画布原生按钮：不创建任何 tk.Button 控件，四枚按钮统一为
                #    "图片/文字条目 + 区域命中"，从根上杜绝控件自绘的
                #    按压边框、下沉残线、假透明 ——
                B = {}          # kind -> {"show": callable(state)}
                img_ids = {}    # kind -> 画布图片条目 id
                b_imgs = {}     # kind -> 当前 PhotoImage（防回收）
                regions = {}    # kind -> (x0, y0, x1, y1) 命中区域
                hover = [None]
                pressed = [None]
                tip = {"win": None, "job": None}

                def _mk(kind, hover_bg, pinned_aware=False):
                    def image(state):
                        ck = (kind, state, pinned_aware and chrome.pinned)
                        im = _cache.get(ck)
                        if im is None:
                            if state == "hover":
                                im = _glyph_box(kind, "#FFFFFF",
                                                _box_image(lambda dy: hover_bg))
                            elif state == "press":
                                color = ("#E9B44C" if (pinned_aware and chrome.pinned)
                                         else "#C7D7F8")
                                im = _glyph_box(kind, color, _press_base_of(kind), off_y=1)
                            else:
                                color = ("#F59E0B" if (pinned_aware and chrome.pinned)
                                         else "#FFFFFF")
                                im = _glyph_box(kind, color, _box_image(_grad_slice))
                            _cache[ck] = im
                        return im
                    def show(state):
                        im = image(state)
                        toolbar.itemconfigure(img_ids[kind], image=im)
                        b_imgs[kind] = im
                    item = toolbar.create_image(0, BTN_Y, image=image("normal"),
                                                anchor="nw")
                    img_ids[kind] = item
                    b_imgs[kind] = image("normal")
                    B[kind] = {"show": show, "hover_bg": hover_bg}

                # "? 关于"：画布文字（真透明）+ 悬停底色矩形条目
                about_bg = toolbar.create_rectangle(0, 0, 1, 1, fill="#3D78E8",
                                                    outline="", state="hidden")
                _about_press_img = _press_base()
                b_imgs["about_press"] = _about_press_img   # 防 PhotoImage 被 GC
                about_img = toolbar.create_image(0, 0, image=_about_press_img,
                                                 anchor="nw", state="hidden")
                about_item = toolbar.create_text(0, 0, text="?", anchor="center",
                                                 fill="#FFFFFF",
                                                 font=("Microsoft YaHei UI", 12, "bold"))
                about_c = [0, 0]

                def _about_show(state):
                    cx, cy = about_c
                    if state == "press":
                        toolbar.coords(about_item, cx, cy + 1)   # 机械按压：文字下沉
                        toolbar.itemconfigure(about_item, fill="#C7D7F8")
                        toolbar.itemconfigure(about_bg, state="hidden")
                        toolbar.itemconfigure(about_img, state="normal")
                    elif state == "hover":
                        toolbar.coords(about_item, cx, cy)
                        toolbar.itemconfigure(about_item, fill="#FFFFFF")
                        toolbar.itemconfigure(about_bg, state="normal")
                        toolbar.itemconfigure(about_img, state="hidden")
                    else:
                        toolbar.coords(about_item, cx, cy)
                        toolbar.itemconfigure(about_item, fill="#FFFFFF")
                        toolbar.itemconfigure(about_bg, state="hidden")
                        toolbar.itemconfigure(about_img, state="hidden")

                def _show_state(kind, state):
                    if kind == "about":
                        _about_show(state)
                    elif kind:
                        B[kind]["show"](state)

                # 预热悬停/按压图（之后状态切换全是换图，无生成延迟）
                _mk("pin", "#3D78E8", pinned_aware=True)
                _mk("minimize", "#3D78E8")
                _mk("close", "#E81123")
                for k in ("pin", "minimize", "close"):
                    B[k]["show"]("hover"); B[k]["show"]("press"); B[k]["show"]("normal")
                _about_show("hover"); _about_show("press"); _about_show("normal")

                def _layout_btns(event=None):
                    x = toolbar.winfo_width() - 14
                    for kind in ("close", "minimize", "pin", "about"):  # 从右往左
                        x -= BOX
                        regions[kind] = (x, BTN_Y, x + BOX, BTN_Y + BOX)
                        if kind == "about":
                            toolbar.coords(about_bg, x, BTN_Y, x + BOX, BTN_Y + BOX)
                            toolbar.coords(about_img, x, BTN_Y)
                            about_c[:] = (x + BOX // 2, BTN_Y + BOX // 2)
                            toolbar.coords(about_item, *about_c)
                        else:
                            toolbar.coords(img_ids[kind], x, BTN_Y)
                        x -= round(4 * _sc)
                toolbar.bind("<Configure>", lambda e: _layout_btns(), add="+")
                toolbar.after_idle(_layout_btns)

                # —— 区域命中 + 事件分发 ——
                TIP_TEXT = {"about": "关于", "minimize": "最小化", "close": "关闭"}

                def _tooltip(kind):
                    if tip["job"]:
                        toolbar.after_cancel(tip["job"]); tip["job"] = None
                    if tip["win"]:
                        tip["win"].destroy(); tip["win"] = None
                    if kind is None:
                        return
                    def _show():
                        text = TIP_TEXT.get(kind) or (
                            "取消置顶" if chrome.pinned else "窗口置顶")
                        w = tk.Toplevel(toolbar)
                        w.overrideredirect(True)
                        w.attributes("-topmost", True)
                        tk.Label(w, text=text, bg="#333333", fg="#FFFFFF",
                                 padx=6, pady=2,
                                 font=("Microsoft YaHei UI", 9)).pack()
                        x0, y0 = regions[kind][0], regions[kind][1]
                        w.geometry("+%d+%d" % (
                            toolbar.winfo_rootx() + x0 + BOX // 2 - 20,
                            toolbar.winfo_rooty() + y0 + BOX + 8))
                        tip["win"] = w
                    tip["job"] = toolbar.after(450, _show)

                def _hit_kind(ev):
                    for kind, (x0, y0, x1, y1) in regions.items():
                        if x0 <= ev.x < x1 and y0 <= ev.y < y1:
                            return kind
                    return None

                def _on_motion(ev):
                    k = _hit_kind(ev)
                    if k != hover[0]:
                        _show_state(hover[0], "normal")
                        hover[0] = k
                        _show_state(k, "hover")
                        toolbar.configure(cursor="hand2" if k else "")
                        _tooltip(k)

                def _on_press(ev):
                    k = _hit_kind(ev)
                    if k is None:
                        return chrome.drag(ev)   # 空白处：转发给 WindowChrome 拖动
                    pressed[0] = k
                    _tooltip(None)
                    _show_state(k, "press")
                    return "break"

                def _on_release(ev):
                    k = pressed[0]
                    pressed[0] = None
                    if k is None:
                        return None
                    inside = _hit_kind(ev) == k
                    if inside:
                        if k == "about":
                            self.show_about()
                        elif k == "pin":
                            chrome.toggle_pin()
                        elif k == "minimize":
                            chrome.minimize()
                        else:
                            chrome.close()
                    try:
                        _show_state(k, "hover" if inside else "normal")
                    except tk.TclError:
                        pass                          # close 后窗口已销毁
                    hover[0] = k if inside else None
                    return "break"

                # 注意：不带 add='+' —— 这会替换 WindowChrome 加在 toolbar 上的
                # 拖动绑定，由 _on_press 自己区分按钮/空白区（空白转发 chrome.drag）
                toolbar.bind("<ButtonPress-1>", _on_press)
                toolbar.bind("<ButtonRelease-1>", _on_release)
                toolbar.bind("<Motion>", _on_motion)
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
