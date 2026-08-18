# -*- coding: utf-8 -*-
"""
run_lock.py —— 跨进程运行锁（防重入 / 防并发损坏）

为什么需要它：
- 同一台机器上，可能同时出现多个「自动更新」执行体：
    · Windows 任务计划（install_autorun.py → app.py --auto-once）
    · 工具 GUI 启动时的 auto_on_launch 后台线程
    · 用户在网页里点「立即运行一次」(/api/app/auto)
    · 服务端内建定时巡检 (_scheduler_loop)
- auto_pipeline 内的 `_running` 只是【进程内】标志，挡不住【跨进程】并发。
- 两个进程同时 save_db / 同时 rmtree+tvbox-dist 会互相踩：db.json 写穿、订阅包半截。

本锁用操作系统级文件锁（Windows=msvcrt.locking / 其它=fcntl.flock），
进程退出（正常或崩溃）时操作系统自动释放，不会留下死锁文件。
"""
import os
import sys
import time

from . import store


def _lock_dir():
    """锁目录：每次调用时按当前 store.BASE_DIR 计算（不缓存），
    以支持测试/工具在运行时重定向 BASE_DIR 而不污染真实项目目录。"""
    return os.path.join(store.BASE_DIR, "output", ".locks")


class RunLockBusy(Exception):
    """已有别的进程持有该锁。"""


def _open_lock_fd(path):
    """以允许并发打开的方式拿到文件描述符（Windows / POSIX 通用）。"""
    flags = os.O_CREAT | os.O_RDWR
    try:
        return os.open(path, flags)
    except OSError:
        # 某些平台在拒绝权限时换个方式
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return os.open(path, flags)


def _try_lock(fd):
    """非阻塞地尝试加排他锁；成功返回 True，已被占用返回 False。"""
    if sys.platform.startswith("win"):
        try:
            import msvcrt
            # 锁第 0 字节；LK_NBLCK=0x01
            msvcrt.locking(fd, 0x01, 1)
            return True
        except (OSError, ImportError, ValueError):
            return False
    else:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (OSError, ImportError, ValueError):
            return False


def _unlock(fd):
    if sys.platform.startswith("win"):
        try:
            import msvcrt
            # 先把文件指针移到 0，再解锁第 0 字节；LK_UNLCK=0x00
            msvcrt.locking(fd, 0x00, 1)
        except (OSError, ImportError, ValueError):
            pass
    else:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
        except (OSError, ImportError, ValueError):
            pass


class RunLock:
    """上下文管理器：with RunLock("auto_run") as lk: ... 同一时刻仅一个进程进入。"""

    def __init__(self, name="auto_run", wait=0):
        self.name = name
        self.wait = wait  # 0=立即返回；>0=最多阻塞等待秒数（轮询）
        self.fd = None
        self.path = os.path.join(_lock_dir(), f"{name}.lock")

    def acquire(self):
        os.makedirs(_lock_dir(), exist_ok=True)
        fd = _open_lock_fd(self.path)
        deadline = time.time() + (self.wait or 0)
        while True:
            if _try_lock(fd):
                self.fd = fd
                # 写入持有者信息（仅诊断用，不影响锁语义）
                try:
                    os.ftruncate(fd, 0)
                    fd_seek0(fd)
                    os.write(fd, f"pid={os.getpid()} at={time.strftime('%Y-%m-%d %H:%M:%S')}\n".encode())
                except Exception:
                    pass
                return True
            if time.time() >= deadline:
                os.close(fd)
                return False
            time.sleep(2)

    def release(self):
        if self.fd is not None:
            try:
                _unlock(self.fd)
            finally:
                try:
                    os.close(self.fd)
                except Exception:
                    pass
                self.fd = None
                # 释放后删除锁文件，避免 .locks 目录长期堆积零散文件。
                # 若此时仍有别的进程持有该锁（Windows 下文件被占用），
                # 删除会失败——忽略即可，不影响锁语义。
                try:
                    if os.path.exists(self.path):
                        os.remove(self.path)
                except Exception:
                    pass

    def __enter__(self):
        if not self.acquire():
            raise RunLockBusy(f"任务「{self.name}」已有其它进程在运行，本次跳过以保证安全。")
        return self

    def __exit__(self, *exc):
        self.release()
        return False


def fd_seek0(fd):
    try:
        os.lseek(fd, 0, os.SEEK_SET)
    except Exception:
        pass


def is_busy(name="auto_run"):
    """快速探测：是否有别的进程正持有该锁（用于状态展示/告警）。

    注意：本进程若已持有该锁（同一 fd），再次尝试加锁会自我冲突，
    因此探测走「独立临时 fd」——仅看文件是否被【别的进程】锁住。
    """
    path = os.path.join(_lock_dir(), f"{name}.lock")
    if not os.path.exists(path):
        return False
    # 用一个独立 fd 试探：若被别的进程持有则加锁失败→忙碌；否则瞬间加解锁→空闲。
    snoop = _open_lock_fd(path)
    try:
        ok = _try_lock(snoop)
        if ok:
            _unlock(snoop)
            return False
        return True
    finally:
        try:
            os.close(snoop)
        except Exception:
            pass
