"""Windows client-side title region, using ctypes only.

Keeps a real taskbar window, system menu, resize borders and Windows maximize.
No overrideredirect(), fake minimize window, WebView, or third-party package.
On non-Windows systems the ordinary OS title bar is retained.

Patched:
1. attach() 时把包装窗口尺寸校正回 wm geometry 指定的客户区尺寸
   （边框已被 WM_NCCALCSIZE 吞掉，否则窗口会以 客户区+边框 的尺寸打开）。
2. WM_GETMINMAXINFO 检测到 Tk 的固定尺寸签名(min==max)时，把 min/max
   track 锁定为当前实际窗口尺寸，修复 resizable(False) 窗口被 Windows
   每次拖动强制撑宽的问题。
3. on_ready 回调 + <<ChromeReady>> 虚拟事件：attach 全部完成（标题栏已
   移除、尺寸已校正）后再延时约 120ms 触发（等 refresh/NCCALCSIZE/DWM
   首帧合成全部落地），用户此刻 reposition/deiconify 窗口看到的即是成品——
   内建的 ready-to-show 钩子。
"""
from __future__ import annotations
import sys
import tkinter as tk
from tkinter import ttk
from .core import icon, Tooltip, PALETTE


class WindowChrome:
    def __init__(self,root,toolbar,drag_widgets=(),on_close=None,sidebar=None,on_ready=None):
        self.root=root
        root._window_chrome=self
        self.close_action=on_close or getattr(root,'close',root.destroy)
        self.on_ready=on_ready
        self._ready_job=None
        self._ready_armed=False
        self._size_settled=False
        self.attached=False
        self._old_proc=None
        self._cache_job=None
        self._drag_rects=[]
        self._excluded=[]
        self.extra_exclusions=[]
        self._drag_anchor=None
        self._drag_job=None
        self._drag_target=None
        self._drag_widget=None
        self._batch_depth=0
        self._native_moving=False
        self._system_move_pending=False
        self.system_move_count=0
        self._fixed_size=None
        self.resize_handles={}
        self.toolbar=toolbar
        self.sidebar=sidebar
        self.drag_widgets=list(drag_widgets)
        if sys.platform!='win32':
            if on_ready is not None:root.after_idle(on_ready)
            return
        import ctypes
        from ctypes import wintypes as w
        self.c,self.w=ctypes,w
        self.u=ctypes.WinDLL('user32',use_last_error=True)
        self.dwm=ctypes.WinDLL('dwmapi',use_last_error=True)
        self.PROC=ctypes.WINFUNCTYPE(ctypes.c_ssize_t,w.HWND,w.UINT,w.WPARAM,w.LPARAM)
        self.get_long=self.u.GetWindowLongPtrW
        self.get_long.argtypes=[w.HWND,ctypes.c_int];self.get_long.restype=ctypes.c_ssize_t
        self.set_long=self.u.SetWindowLongPtrW
        self.set_long.argtypes=[w.HWND,ctypes.c_int,ctypes.c_void_p];self.set_long.restype=ctypes.c_void_p
        self.u.CallWindowProcW.argtypes=[ctypes.c_void_p,w.HWND,w.UINT,w.WPARAM,w.LPARAM]
        self.u.CallWindowProcW.restype=ctypes.c_ssize_t
        self.u.GetParent.argtypes=[w.HWND];self.u.GetParent.restype=w.HWND
        self.u.SetWindowPos.argtypes=[w.HWND,w.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.UINT]
        self.u.RedrawWindow.argtypes=[w.HWND,ctypes.c_void_p,w.HANDLE,w.UINT]
        self.u.GetWindowRect.argtypes=[w.HWND,ctypes.POINTER(w.RECT)]
        self.u.IsZoomed.argtypes=[w.HWND]
        self.u.SendMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM];self.u.SendMessageW.restype=ctypes.c_ssize_t
        self.u.PostMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM];self.u.PostMessageW.restype=w.BOOL
        self.u.DefWindowProcW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM];self.u.DefWindowProcW.restype=ctypes.c_ssize_t
        self.u.MonitorFromWindow.argtypes=[w.HWND,w.DWORD];self.u.MonitorFromWindow.restype=w.HANDLE
        self.u.GetMonitorInfoW.argtypes=[w.HANDLE,ctypes.c_void_p]
        self.dwm.DwmSetWindowAttribute.argtypes=[w.HWND,w.DWORD,ctypes.c_void_p,w.DWORD]
        self.dwm.DwmExtendFrameIntoClientArea.argtypes=[w.HWND,ctypes.c_void_p]
        class MonitorInfo(ctypes.Structure):
            _fields_=[('cbSize',w.DWORD),('rcMonitor',w.RECT),('rcWork',w.RECT),('dwFlags',w.DWORD)]
        class MinMaxInfo(ctypes.Structure):
            _fields_=[('ptReserved',w.POINT),('ptMaxSize',w.POINT),('ptMaxPosition',w.POINT),('ptMinTrackSize',w.POINT),('ptMaxTrackSize',w.POINT)]
        self.MonitorInfo,self.MinMaxInfo=MonitorInfo,MinMaxInfo
        self.controls=ttk.Frame(toolbar)
        self.controls.pack(side='right',padx=(18,0))
        self.pinned=False
        self.buttons={}
        self._drawn_controls={}
        self.icon_size=round(root.winfo_fpixels('1i')/96*14)
        for key,command,label in [('pin',self.toggle_pin,'窗口置顶'),('minimize',self.minimize,'最小化'),
                                  ('maximize',self.toggle_maximize,'最大化 / 还原'),('close',self.close,'关闭')]:
            button=ttk.Button(self.controls,image=icon(root,key,self.icon_size),
                              style='Close.TButton' if key=='close' else 'Quiet.TButton',
                              command=command,takefocus=False,padding=(12,9))
            button.pack(side='left',padx=1)
            self.buttons[key]=button
            button.bind('<Enter>',lambda event,k=key:self.paint_control(k,True),add='+')
            button.bind('<Leave>',lambda event,k=key:self.paint_control(k,False),add='+')
            Tooltip(button,(lambda:'取消置顶' if self.pinned else '窗口置顶') if key=='pin' else label)
        self.max_button=self.buttons['maximize']
        for widget in self.drag_widgets:
            widget.bind('<ButtonPress-1>',self.drag,add='+')
            widget.bind('<B1-Motion>',self.drag_motion,add='+')
            widget.bind('<ButtonRelease-1>',self.end_drag,add='+')
            widget.bind('<Double-Button-1>',self.double_click,add='+')
        root.bind('<Escape>',self.cancel_drag,add='+')
        root.bind('<Configure>',self.schedule_cache,add='+')
        root.bind('<Map>',self.schedule_cache,add='+')
        self._attach_job=root.after_idle(self.attach)

    def attach(self):
        if self.attached:return
        self._attach_job=None
        self.root.update_idletasks()
        # Tk creates its native wrapper after the first map. Subclassing the
        # client HWND before then would leave the real OS caption untouched.
        wrapper=self.u.GetParent(self.root.winfo_id())
        if not wrapper:
            self._attach_job=self.root.after(20,self.attach)
            return
        self.hwnd=wrapper
        self.original_style=self.get_long(self.hwnd,-16)
        self.original_exstyle=self.get_long(self.hwnd,-20)
        self._callback=self.PROC(self.window_proc)  # Must stay alive for the window lifetime.
        self._old_proc=self.set_long(self.hwnd,-4,self.c.cast(self._callback,self.c.c_void_p))
        if not self._old_proc:return
        self.attached=True
        # Keep standard window capabilities for native Snap/animations. The
        # caption is hidden by WM_NCCALCSIZE, not by stripping capability flags.
        style=self.original_style|0x00C00000|0x00040000|0x00080000|0x00020000|0x00010000
        self.set_long(self.hwnd,-16,self.c.c_void_p(style))
        self.set_long(self.hwnd,-20,self.c.c_void_p((self.original_exstyle|0x00040000)&~0x00000080))
        margins=(self.c.c_int*4)(1,1,1,1)
        self.dwm.DwmExtendFrameIntoClientArea(self.hwnd,self.c.byref(margins))
        corner=self.c.c_int(2)
        self.dwm.DwmSetWindowAttribute(self.hwnd,33,self.c.byref(corner),self.c.sizeof(corner))
        self.u.SetWindowPos(self.hwnd,None,0,0,0,0,0x0001|0x0002|0x0004|0x0010|0x0020)
        # PATCH(1): wm geometry 的宽高是客户区尺寸，Tk 按带边框样式计算包装
        # 窗口尺寸；WM_NCCALCSIZE 返回 0 后客户区==整个包装窗口，若不校正，
        # 窗口会以 客户区+边框 的尺寸打开（视觉上"窗口很宽/很高"）。
        try:
            size=self.root.wm_geometry().split('+')[0].split('-')[0]  # "640x420"
            w_req,h_req=(int(v) for v in size.split('x'))
            # 固定尺寸锁定的基准：wm geometry 请求的客户区尺寸。
            # 必须是常量——最小化还原的过渡过程中，WM_SIZE/窗口矩形
            # 都可能是任务栏小条的尺寸，读运行时消息会锁错。
            self._fixed_size=(w_req,h_req)
            self.u.SetWindowPos(self.hwnd,None,0,0,w_req,h_req,0x0002|0x0004|0x0010)
        except Exception:
            pass
        self.install_resize_handles()
        self.schedule_cache()
        # PATCH(3): ready-to-show 钩子，事件驱动而非定时猜测：
        # 等"客户端尺寸落到 _fixed_size 的那组 WM_SIZE"——它是
        # NCCALCSIZE 重排全部落地的精确信号（见 window_proc 置标志），
        # 由随后的 refresh_cache（跑在 idle，Tcl 安全）接管触发。
        # 300ms 兜底仅防御极端情况（正常路径 WM_SIZE 必到）。
        self._ready_armed=True
        self._size_settled=False
        self._ready_job=self.root.after(300,self._fire_ready)

    def _fire_ready(self):
        self._ready_job=None
        if self.on_ready is not None:
            try:self.on_ready()
            except Exception:pass
        try:self.root.event_generate('<<ChromeReady>>')
        except tk.TclError:pass

    def install_resize_handles(self):
        # Tk child HWNDs cover the client edges after WM_NCCALCSIZE removes
        # the frame. Wrapper-only WM_NCHITTEST therefore misses real mouse
        # input there. Narrow client hit targets forward to Windows SC_SIZE.
        for name,direction,cursor in [('left',1,'size_we'),('right',2,'size_we'),
                ('top',3,'size_ns'),('topleft',4,'size_nw_se'),('topright',5,'size_ne_sw'),
                ('bottom',6,'size_ns'),('bottomleft',7,'size_ne_sw'),('bottomright',8,'size_nw_se')]:
            # Only sidebar layouts have a grey left edge. Plain windows must
            # blend all resize targets, including both left corners, into bg.
            color=PALETTE['sidebar'] if self.sidebar is not None and 'left' in name else PALETTE['bg']
            handle=tk.Frame(self.root,bg=color,borderwidth=0,highlightthickness=0,cursor=cursor,takefocus=False)
            handle.bind('<ButtonPress-1>',lambda event,d=direction:self.start_resize(d,event))
            if name in ('top','bottom'):
                handle.sidebar_fill=tk.Frame(handle,bg=PALETTE['sidebar'],borderwidth=0,highlightthickness=0,cursor=cursor)
                handle.sidebar_fill.bind('<ButtonPress-1>',lambda event,d=direction:self.start_resize(d,event))
            self.resize_handles[name]=handle
        self.layout_resize_handles()

    def layout_resize_handles(self):
        if not self.resize_handles:return
        if self.u.IsZoomed(self.hwnd):
            for handle in self.resize_handles.values():handle.place_forget()
            return
        edge=getattr(self,'resize_margin',7);corner=edge*2
        positions={
            'left':dict(x=0,y=corner,width=edge,relheight=1,height=-2*corner),
            'right':dict(relx=1,x=-edge,y=corner,width=edge,relheight=1,height=-2*corner),
            'top':dict(x=corner,y=0,relwidth=1,width=-2*corner,height=edge),
            'bottom':dict(x=corner,rely=1,y=-edge,relwidth=1,width=-2*corner,height=edge),
            'topleft':dict(x=0,y=0,width=corner,height=corner),
            'topright':dict(relx=1,x=-corner,y=0,width=corner,height=corner),
            'bottomleft':dict(x=0,rely=1,y=-corner,width=corner,height=corner),
            'bottomright':dict(relx=1,x=-corner,rely=1,y=-corner,width=corner,height=corner)}
        for name,handle in self.resize_handles.items():
            handle.place(**positions[name]);handle.lift()
            if hasattr(handle,'sidebar_fill'):
                sidebar=self.sidebar
                width=max(0,sidebar.winfo_width()-corner) if sidebar is not None else 0
                if width:handle.sidebar_fill.place(x=0,y=0,width=width,relheight=1)
                else:handle.sidebar_fill.place_forget()

    def start_resize(self,direction,event=None):
        if not self.attached or self.u.IsZoomed(self.hwnd):return 'break'
        if self.root.grab_current() is not None:return 'break'
        self.u.ReleaseCapture()
        self._system_move_pending=True
        packed=0 if event is None else (event.x_root&0xffff)|((event.y_root&0xffff)<<16)
        self.u.PostMessageW(self.hwnd,0x0112,0xf000|direction,packed)
        return 'break'

    def work_area(self):
        info=self.MonitorInfo();info.cbSize=self.c.sizeof(info)
        monitor=self.u.MonitorFromWindow(self.hwnd,2)
        return info if self.u.GetMonitorInfoW(monitor,self.c.byref(info)) else None

    def window_proc(self,hwnd,message,wparam,lparam):
        try:
            if message==0x0005 and self._ready_armed and wparam==0:  # WM_SIZE/SIZE_RESTORED
                # 客户端尺寸落到请求值 = 重排全部落地的信号。只置标志：
                # window_proc 不重入 Tcl，由随后的 refresh_cache 接管触发
                if self._fixed_size is not None and \
                   (lparam&0xffff,(lparam>>16)&0xffff)==self._fixed_size:
                    self._size_settled=True
            if message==0x0231:  # WM_ENTERSIZEMOVE
                self._native_moving=True;self._system_move_pending=False
                self.system_move_count+=1
            elif message==0x0232:  # WM_EXITSIZEMOVE
                self._native_moving=False;self._system_move_pending=False
            if (message in (0x00A1,0x00A3) and (wparam==2 or 10<=wparam<=17)) or (message==0x0112 and wparam&0xfff0 in (0xf000,0xf010)):
                # Use Windows' caption/move handler, not Tk's translated mouse
                # bindings. This enables native Snap without synchronous Tcl
                # callback -> SendMessage -> Tcl callback recursion.
                result=self.u.DefWindowProcW(hwnd,message,wparam,lparam)
                self._system_move_pending=False
                return result
            if message==0x0085:  # WM_NCPAINT: no OS caption to paint, including inactive popups.
                return 0
            if message==0x0086:  # WM_NCACTIVATE: retain client chrome when focus leaves the window.
                return 1
            if message==0x0083 and wparam:  # WM_NCCALCSIZE: the toolbar owns the top client area.
                if self.u.IsZoomed(hwnd):
                    info=self.work_area()
                    if info:
                        rect=self.c.cast(lparam,self.c.POINTER(self.w.RECT)).contents
                        rect.left,rect.top,rect.right,rect.bottom=info.rcWork.left,info.rcWork.top,info.rcWork.right,info.rcWork.bottom
                return 0
            if message==0x0024:  # WM_GETMINMAXINFO, including multi-monitor taskbar bounds.
                result=self.u.CallWindowProcW(self._old_proc,hwnd,message,wparam,lparam)
                # PATCH(2): Tk 给固定尺寸窗口的 min/max track 加了 2×边框宽，
                # 但边框已被 WM_NCCALCSIZE 吞进客户区，Windows 每次拖动都按
                # track 强制校正，窗口越拖越宽。检测到 Tk 的固定尺寸签名
                # (min==max) 时直接锁定为当前实际窗口尺寸——不猜差值，且对
                # 任何 Tk 版本/任何 DPI 都成立。
                limits=self.c.cast(lparam,self.c.POINTER(self.MinMaxInfo)).contents
                fixed=limits.ptMinTrackSize.x==limits.ptMaxTrackSize.x and limits.ptMinTrackSize.y==limits.ptMaxTrackSize.y
                if fixed:
                    # 用 attach 时存下的请求尺寸常量，不用任何运行时读数：
                    # 最小化还原的过渡瞬间，窗口矩形/WM_SIZE 都还是任务栏
                    # 小条的尺寸，读瞬时值会把 track 锁成最小化尺寸
                    if self._fixed_size is not None:
                        cur_w,cur_h=self._fixed_size
                    else:
                        rect=self.w.RECT();self.u.GetWindowRect(hwnd,self.c.byref(rect))
                        cur_w=rect.right-rect.left;cur_h=rect.bottom-rect.top
                        limits.ptMaxPosition.x=rect.left;limits.ptMaxPosition.y=rect.top
                    limits.ptMinTrackSize.x=limits.ptMaxTrackSize.x=cur_w
                    limits.ptMinTrackSize.y=limits.ptMaxTrackSize.y=cur_h
                    # 固定尺寸窗口同时禁用最大化：否则最大化按钮/双击标题栏/
                    # Win+↑ 仍能绕开 resizable(False) 改变窗口尺寸
                    limits.ptMaxSize.x=cur_w;limits.ptMaxSize.y=cur_h
                info=self.work_area()
                if info and not fixed:
                    limits=self.c.cast(lparam,self.c.POINTER(self.MinMaxInfo)).contents
                    limits.ptMaxPosition.x=info.rcWork.left-info.rcMonitor.left
                    limits.ptMaxPosition.y=info.rcWork.top-info.rcMonitor.top
                    limits.ptMaxSize.x=info.rcWork.right-info.rcWork.left
                    limits.ptMaxSize.y=info.rcWork.bottom-info.rcWork.top
                return result
            if message==0x0084:  # WM_NCHITTEST, all pixel calculations; no Tk re-entry.
                x=self.c.c_short(lparam&0xffff).value;y=self.c.c_short((lparam>>16)&0xffff).value
                rect=self.w.RECT();self.u.GetWindowRect(hwnd,self.c.byref(rect))
                edge=getattr(self,'resize_margin',7)
                left=x<rect.left+edge;right=x>=rect.right-edge
                top=y<rect.top+edge;bottom=y>=rect.bottom-edge
                if not self.u.IsZoomed(hwnd):
                    if top and left:return 13
                    if top and right:return 14
                    if bottom and left:return 16
                    if bottom and right:return 17
                    if left:return 10
                    if right:return 11
                    if top:return 12
                    if bottom:return 15
                contains=lambda r:r[0]<=x<r[2] and r[1]<=y<r[3]
                if any(contains(r) for r in self._excluded):return 1
                if any(contains(r) for r in self._drag_rects):return 2
        except Exception:
            # A platform-specific edge case must not crash the window procedure.
            pass
        return self.u.CallWindowProcW(self._old_proc,hwnd,message,wparam,lparam)

    def schedule_cache(self,event=None):
        if event is not None and event.widget is not self.root:return
        if self._cache_job is None:self._cache_job=self.root.after_idle(self.refresh_cache)

    def refresh_cache(self):
        self._cache_job=None
        if not self.attached:return
        if self._ready_armed and self._size_settled:
            # 尺寸消息已落地，且事件队列已抽空（refresh 本身跑在 idle）：
            # 这就是"真 ready"时刻——取消兜底定时，一帧后回调
            self._ready_armed=False
            if self._ready_job is not None:
                try:self.root.after_cancel(self._ready_job)
                except tk.TclError:pass
                self._ready_job=None
            self._ready_job=self.root.after_idle(self._fire_ready)
        self.resize_margin=max(5,round(self.root.winfo_fpixels('1i')/96*5))
        self.layout_resize_handles()
        def rect(widget):
            x,y=widget.winfo_rootx(),widget.winfo_rooty()
            return x,y,x+widget.winfo_width(),y+widget.winfo_height()
        self._drag_rects=[rect(widget) for widget in self.drag_widgets if widget.winfo_exists()]
        self._excluded=[rect(widget) for widget in self.toolbar.winfo_children()
                        if widget not in self.drag_widgets and widget.winfo_ismapped()]
        self._excluded.extend(rect(widget) for widget in self.extra_exclusions if widget.winfo_exists() and widget.winfo_ismapped())
        self.paint_control('maximize',False)

    def paint_control(self,key,hover=False):
        pinned=key=='pin' and self.pinned
        background=('#FFE8E8' if key=='close' else PALETTE['hover']) if hover else PALETTE['selected'] if pinned else PALETTE['bg']
        color='#DD3333' if key=='close' and hover else PALETTE['blue'] if pinned else PALETTE['muted']
        symbol='restore' if key=='maximize' and self.attached and self.u.IsZoomed(self.hwnd) else key
        signature=(symbol,color,background,pinned)
        if self._drawn_controls.get(key)==signature:return
        self._drawn_controls[key]=signature
        self.buttons[key].configure(image=icon(self.root,symbol,self.icon_size,color=color,background=background),
                                   style='Close.TButton' if key=='close' else 'CardIcon.TButton' if pinned else 'Quiet.TButton')

    def toggle_pin(self):
        self.pinned=not self.pinned
        self.root.attributes('-topmost',self.pinned)
        self.paint_control('pin')

    def drag(self,event):
        if not self.attached:return
        if self._native_moving or self._system_move_pending:return 'break'
        self._system_move_pending=True
        self.u.ReleaseCapture()
        point=((int(event.y_root)&0xffff)<<16)|(int(event.x_root)&0xffff)
        # Child HWND fallback: post a system command and return to Tk first.
        # Never synchronously send WM_NCLBUTTONDOWN from a Tk mouse callback.
        if not self.u.PostMessageW(self.hwnd,0x0112,0xF012,point):self._system_move_pending=False
        return 'break'

    def drag_motion(self,event):
        if self._drag_anchor is None:return
        x,y,left,top=self._drag_anchor
        self._drag_target=(left+event.x_root-x,top+event.y_root-y)
        if self._drag_job is None:self._drag_job=self.root.after(12,self.flush_drag)
        return 'break'

    def flush_drag(self):
        self._drag_job=None
        if self._drag_target is not None and self.attached:
            x,y=self._drag_target;self._drag_target=None
            self.u.SetWindowPos(self.hwnd,None,round(x),round(y),0,0,0x0001|0x0004|0x0010)

    def end_drag(self,event=None):
        if self._drag_job is not None:
            self.root.after_cancel(self._drag_job);self._drag_job=None
        self.flush_drag()
        self._drag_anchor=None
        if self._drag_widget is not None:
            try:self._drag_widget.grab_release()
            except tk.TclError:pass
            self._drag_widget=None
        return 'break'

    def cancel_drag(self,event=None):
        if self._native_moving or self._system_move_pending:
            self.u.PostMessageW(self.hwnd,0x001F,0,0)
            self._system_move_pending=False
            return 'break'
        if self._drag_anchor is None and self._drag_job is None:return None
        self._drag_target=None
        return self.end_drag(event)

    def double_click(self,event):
        self.end_drag()
        self.toggle_maximize();return 'break'

    def toggle_maximize(self):
        self.begin_update()
        try:
            self.root.state('normal' if self.root.state()=='zoomed' else 'zoomed')
            self.root.update_idletasks()
        finally:self.end_update()
        self.schedule_cache()

    def begin_update(self):
        if self.attached:
            if self._batch_depth==0:self.u.SendMessageW(self.hwnd,0x000B,0,0)
            self._batch_depth+=1

    def end_update(self):
        if self.attached and self._batch_depth:
            self._batch_depth-=1
            if self._batch_depth==0:
                self.u.SendMessageW(self.hwnd,0x000B,1,0)
                self.u.RedrawWindow(self.hwnd,None,None,0x0001|0x0080|0x0100|0x0400)

    def minimize(self):self.root.iconify()

    def close(self):
        self.detach()
        self.close_action()

    def detach(self):
        if sys.platform!='win32':
            if on_ready is not None:root.after_idle(on_ready)
            return
        self.cancel_drag()
        for handle in self.resize_handles.values():
            if handle.winfo_exists():handle.destroy()
        self.resize_handles.clear()
        while self._batch_depth:self.end_update()
        for job in (self._cache_job,getattr(self,'_attach_job',None),self._ready_job):
            if job is not None:
                try:self.root.after_cancel(job)
                except tk.TclError:pass
        if self.attached and self._old_proc:
            self.set_long(self.hwnd,-4,self.c.c_void_p(self._old_proc))
            self.attached=False
